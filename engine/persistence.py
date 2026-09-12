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
    role TEXT, quantity_kwh REAL, unit_price_inr REAL, energy_inr REAL,
    transaction_inr REAL, wheeling_inr REAL, cross_subsidy_inr REAL,
    storage_fee_inr REAL, ageing_inr REAL, net_inr REAL);
CREATE TABLE IF NOT EXISTS transformer_state (
    block INTEGER PRIMARY KEY, state_json TEXT);
"""


class Persistence:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self.db = sqlite3.connect(self.path)
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ------------------------------------------------------------- writing

    def write_block(self, block: int, ticks: list[MeterTick], orders: list[Order],
                    trades: list[Trade], bills: list[BillLine] | None,
                    ageing: AgeingResult | None = None) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO meter_tick VALUES (?,?,?,?,?)",
            [(t.block, t.house_id, t.load_kwh, t.gen_kwh, t.ambient_c) for t in ticks])
        self.db.executemany(
            "INSERT OR REPLACE INTO order_book VALUES (?,?,?,?,?,?)",
            [(o.order_id, o.block, o.house_id, o.side, o.quantity_kwh, o.limit_price)
             for o in orders])
        self.db.executemany(
            "INSERT OR REPLACE INTO trade VALUES (?,?,?,?,?,?,?)",
            [(t.trade_id, t.block, t.seller_id, t.buyer_id, t.quantity_kwh,
              t.clearing_price, t.curtailed_fraction) for t in trades])
        if bills:
            self.db.executemany(
                "INSERT OR REPLACE INTO bill_line VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(b.line_id, b.block, b.trade_id, b.house_id, b.role, b.quantity_kwh,
                  b.unit_price_inr, b.energy_inr, b.transaction_inr, b.wheeling_inr,
                  b.cross_subsidy_inr, b.storage_fee_inr, b.ageing_inr, b.net_inr)
                 for b in bills])
        if ageing is not None:
            self.save_transformer_state(block, {
                "cumulative_life_hours": ageing.cumulative_life_hours,
                "ageing_adder": ageing.ageing_adder,
            })
        self.db.commit()

    def save_transformer_state(self, block: int, state: dict) -> None:
        """Every block. A state written only at shutdown is a state you do not
        have when it matters."""
        self.db.execute(
            "INSERT OR REPLACE INTO transformer_state VALUES (?,?)",
            (block, json.dumps(state, sort_keys=True)))
        self.db.commit()

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
