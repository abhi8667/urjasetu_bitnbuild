"""SQLite persistence. Owner: B.

Five tables. Only `transformer_state` survives a restart, and it is written
every block rather than at shutdown — a run that dies at block 500 must be able
to resume from block 500, not from the last clean exit that never happened.

PS1: hard-kill mid-run, restart, finish. Cumulative loss of life matches an
uninterrupted run exactly. No gap, no double count.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from engine.domain import AgeingResult, BillLine, MeterTick, Order, Trade

#: Errors worth a second attempt: the database was locked, busy, or the disk
#: hiccuped. Retrying costs a millisecond and usually works.
_TRANSIENT = (sqlite3.OperationalError, sqlite3.DatabaseError)

#: Errors a retry cannot help. Bad SQL, a violated constraint, a closed
#: connection — these are bugs, and retrying one just hides it for a moment.
#: Same lesson as the blanket `except Exception` that hid three contract breaks
#: in the flow agent: catch narrowly, or you catch your own mistakes.
_FATAL = (sqlite3.ProgrammingError, sqlite3.IntegrityError, sqlite3.InterfaceError,
          sqlite3.NotSupportedError)


class PersistenceError(RuntimeError):
    """A write failed twice. PRD §12: do not continue with unpersisted state."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS meter_tick (
    block INTEGER, house_id TEXT, load_kwh REAL, gen_kwh REAL, ambient_c REAL,
    PRIMARY KEY (block, house_id));
CREATE TABLE IF NOT EXISTS order_book (
    order_id TEXT PRIMARY KEY, block INTEGER, house_id TEXT, side TEXT,
    quantity_kwh REAL, limit_price REAL);
CREATE TABLE IF NOT EXISTS trade (
    trade_id TEXT PRIMARY KEY, block INTEGER, seller_id TEXT, buyer_id TEXT,
    quantity_kwh REAL, clearing_price REAL, curtailed_fraction REAL);
CREATE TABLE IF NOT EXISTS bill_line (
    line_id TEXT PRIMARY KEY, block INTEGER, trade_id TEXT, house_id TEXT,
    role TEXT, quantity_kwh REAL, loss_kwh REAL, unit_price_inr REAL,
    energy_inr REAL, transaction_inr REAL, wheeling_inr REAL,
    cross_subsidy_inr REAL, storage_fee_inr REAL, ageing_inr REAL,
    platform_inr REAL, gst_inr REAL, net_inr REAL);
CREATE TABLE IF NOT EXISTS transformer_state (
    block INTEGER PRIMARY KEY, state_json TEXT);
"""


class Persistence:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self.db = sqlite3.connect(self.path)
        self.db.executescript(SCHEMA)
        self.db.commit()
        #: How many writes needed their second attempt. Nonzero is not a
        #: failure, but a run summary that quietly retried a hundred times is
        #: telling you something about the disk.
        self.retries_used = 0

    # ------------------------------------------------------------- writing

    def _atomic(self, statements: list[tuple[str, list]], what: str) -> None:
        """Run every statement in ONE transaction, retrying once (PRD §12).

        Atomicity is the half of this that is easy to miss. write_block used to
        issue four separate executemany calls and then commit, with a nested
        save_transformer_state committing in the middle of them. A failure
        partway through left some tables written and others not, and — worse —
        the next block's commit would sweep that partial state in alongside its
        own. "Do not continue with unpersisted state" has to mean the block
        either lands whole or not at all.

        `with self.db` commits on a clean exit and rolls back on any exception,
        so a failed attempt leaves nothing behind to sweep up later.
        """
        last: Exception | None = None
        for attempt in (1, 2):
            try:
                with self.db:
                    for sql, params in statements:
                        if not params:
                            continue
                        self.db.executemany(sql, params)
                self.retries_used += attempt - 1
                return
            except _FATAL:
                # Not a blip. Let it out with the stack intact.
                raise
            except _TRANSIENT as exc:
                last = exc
                try:
                    self.db.rollback()
                except sqlite3.Error:
                    pass            # already rolled back, or the handle is gone
        raise PersistenceError(
            f"{what} failed twice; refusing to continue with unpersisted state"
        ) from last

    def write_block(self, block: int, ticks: list[MeterTick], orders: list[Order],
                    trades: list[Trade], bills: list[BillLine] | None,
                    ageing: AgeingResult | None = None) -> None:
        statements = [
            ("INSERT OR REPLACE INTO meter_tick VALUES (?,?,?,?,?)",
             [(t.block, t.house_id, t.load_kwh, t.gen_kwh, t.ambient_c) for t in ticks]),
            ("INSERT OR REPLACE INTO order_book VALUES (?,?,?,?,?,?)",
             [(o.order_id, o.block, o.house_id, o.side, o.quantity_kwh, o.limit_price)
              for o in orders]),
            ("INSERT OR REPLACE INTO trade VALUES (?,?,?,?,?,?,?)",
             [(t.trade_id, t.block, t.seller_id, t.buyer_id, t.quantity_kwh,
               t.clearing_price, t.curtailed_fraction) for t in trades]),
            # loss_kwh, platform_inr and gst_inr are columns now. BillLine has
            # always carried loss_kwh and the table had nowhere to put it, so
            # transmission loss survived only in memory and a resumed run had no
            # record of it at all.
            ("INSERT OR REPLACE INTO bill_line VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             [(b.line_id, b.block, b.trade_id, b.house_id, b.role, b.quantity_kwh,
               b.loss_kwh, b.unit_price_inr, b.energy_inr, b.transaction_inr,
               b.wheeling_inr, b.cross_subsidy_inr, b.storage_fee_inr,
               b.ageing_inr, b.platform_inr, b.gst_inr, b.net_inr)
              for b in (bills or [])]),
        ]
        if ageing is not None:
            # Part of the same transaction, not a separate commit partway
            # through: transformer_state is the one table that survives a
            # restart, and it must never be ahead of or behind the block it
            # describes.
            statements.append((
                "INSERT OR REPLACE INTO transformer_state VALUES (?,?)",
                [(block, json.dumps({"life_used_frac": ageing.life_used_frac,
                                     "ageing_adder": ageing.ageing_adder,
                                     "next_ageing_adder": ageing.next_ageing_adder},
                                    sort_keys=True))]))
        self._atomic(statements, f"block {block}")

    def save_transformer_state(self, block: int, state: dict) -> None:
        """Every block. A state written only at shutdown is a state you do not
        have when it matters."""
        self._atomic(
            [("INSERT OR REPLACE INTO transformer_state VALUES (?,?)",
              [(block, json.dumps(state, sort_keys=True))])],
            f"transformer_state at block {block}")

    # ------------------------------------------------------------- reading

    def load_transformer_state(self) -> tuple[int, dict] | None:
        """The last state written, and the block it belongs to. Resume from
        `block + 1` — the block in the row is one that completed."""
        row = self.db.execute(
            "SELECT block, state_json FROM transformer_state "
            "ORDER BY block DESC LIMIT 1").fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def count(self, table: str) -> int:
        return self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def close(self) -> None:
        self.db.close()
