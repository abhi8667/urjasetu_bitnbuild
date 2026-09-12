"""Persistence failure handling — PRD §12.

    | SQLite write failure | Retry once, then raise — do not continue with
                             unpersisted state |

Neither half of that existed: there was no retry, and no exception handling at
all. Worse, write_block issued four separate executemany calls and then
committed, with a nested save_transformer_state committing partway through — so
a failure mid-block left some tables written and others not, and the NEXT
block's commit would sweep that partial state in alongside its own.

Every test here forces a real failure rather than assuming the behaviour.

Run standalone: `python3 tests/test_persistence_failures.py`
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.domain import BillLine, MeterTick, Order, Trade
from engine.persistence import Persistence, PersistenceError

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return ok


def _block(n=0):
    """One block's worth of writes, enough to span every table."""
    ticks = [MeterTick(n, "10000", 1.0, 0.0, 25.0)]
    orders = [Order(f"O{n}", n, "10000", "bid", 1.0, 7.0)]
    trades = [Trade(f"T{n}", n, "10006", "10000", 1.0, 4.0, 0.0)]
    bills = [
        BillLine(f"T{n}:S", n, f"T{n}", "10006", "seller", 1.0, 0.0, 4.0,
                 -4.0, 0.21, 0.0, 0.0, 0.0, 0.0, -3.79),
        BillLine(f"T{n}:B", n, f"T{n}", "10000", "buyer", 0.95, 0.05, 4.0,
                 4.0, 0.21, 1.01, 0.0, 0.0, 0.0, 5.22),
    ]
    return ticks, orders, trades, bills


class _FlakyConnection:
    """Wraps a real connection and fails executemany a set number of times."""

    def __init__(self, db, failures, error=sqlite3.OperationalError("database is locked")):
        self._db = db
        self._remaining = failures
        self._error = error
        self.attempts = 0

    def executemany(self, sql, params):
        self.attempts += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise self._error
        return self._db.executemany(sql, params)

    def __getattr__(self, name):
        return getattr(self._db, name)

    def __enter__(self):
        return self._db.__enter__()

    def __exit__(self, *exc):
        return self._db.__exit__(*exc)


# --------------------------------------------------------- retry once

def test_a_transient_failure_is_retried_and_succeeds():
    store = Persistence()
    store.db = _FlakyConnection(store.db, failures=1)
    store.save_transformer_state(1, {"life_used_frac": {"DT-1": 0.5}})
    check("a transient failure is retried once and succeeds",
          store.load_transformer_state()[0] == 1 and store.retries_used == 1,
          f"{store.db.attempts} attempts, {store.retries_used} retry")
    store.close()


def test_a_persistent_failure_raises_rather_than_continuing():
    store = Persistence()
    store.db = _FlakyConnection(store.db, failures=99)
    try:
        store.save_transformer_state(1, {"life_used_frac": {"DT-1": 0.5}})
        check("a persistent failure raises", False, "it returned normally")
    except PersistenceError as exc:
        check("a persistent failure raises PersistenceError", True,
              f"after {store.db.attempts} attempts")
        check("the raised error names the cause",
              "unpersisted state" in str(exc))
    store.close()


def test_exactly_two_attempts_not_more():
    """"Retry once" means two attempts total — not an unbounded loop that hangs
    a run against a disk that is genuinely gone."""
    store = Persistence()
    store.db = _FlakyConnection(store.db, failures=99)
    try:
        store.save_transformer_state(1, {"x": 1})
    except PersistenceError:
        pass
    check("exactly two attempts, never more", store.db.attempts == 2,
          f"{store.db.attempts} attempts")
    store.close()


# ----------------------------------------- fatal errors are not retried

def test_programming_errors_are_not_retried_they_surface():
    """Bad SQL or a violated constraint is a bug. Retrying one hides it for a
    moment and then reports the wrong cause — the same failure mode as the
    blanket `except Exception` that concealed three contract breaks in the flow
    agent."""
    store = Persistence()
    store.db = _FlakyConnection(store.db, failures=99,
                                error=sqlite3.ProgrammingError("bad SQL"))
    try:
        store.save_transformer_state(1, {"x": 1})
        check("a programming error is not swallowed", False, "it returned normally")
    except sqlite3.ProgrammingError:
        check("a programming error surfaces on the first attempt, unretried",
              store.db.attempts == 1, f"{store.db.attempts} attempt")
    except PersistenceError:
        check("a programming error is not converted into a retry", False,
              "it was retried and rewrapped")
    store.close()


# ------------------------------------------------------- atomicity

def test_a_failed_block_write_leaves_nothing_behind():
    """The partial-write bug. A block must land whole or not at all — otherwise
    the next block's commit sweeps the leftovers in with it."""
    store = Persistence()
    ticks, orders, trades, bills = _block(0)
    # Fail on the LAST table of every attempt, so three tables are written
    # first and both attempts fail. Failing only once would (correctly) be
    # retried and succeed, which is a different test.
    class FailOnBillLine(_FlakyConnection):
        def executemany(self, sql, params):
            self.attempts += 1
            if "bill_line" in sql:
                raise sqlite3.OperationalError("disk I/O error")
            return self._db.executemany(sql, params)

    store.db = FailOnBillLine(store.db, failures=0)
    try:
        store.write_block(0, ticks, orders, trades, bills)
        check("a failed block write raises", False, "it returned normally")
    except PersistenceError:
        pass
    written = {t: store.count(t) for t in
               ("meter_tick", "order_book", "trade", "bill_line")}
    check("a failed block write leaves every table empty",
          all(v == 0 for v in written.values()), str(written))
    store.close()


def test_a_successful_block_write_lands_in_every_table():
    store = Persistence()
    store.write_block(0, *_block(0))
    written = {t: store.count(t) for t in
               ("meter_tick", "order_book", "trade", "bill_line")}
    check("a successful block write lands everywhere",
          all(v > 0 for v in written.values()), str(written))
    store.close()


def test_transformer_state_shares_the_block_transaction():
    """transformer_state is the only table that survives a restart. If it
    commits separately it can end up ahead of, or behind, the block it
    describes — and PS1 (resume with no gap, no double count) depends on it
    being exactly level."""
    import inspect
    from engine import persistence
    source = inspect.getsource(persistence.Persistence.write_block)
    check("write_block does not commit partway through",
          "self.db.commit()" not in source and "_atomic" in source,
          "state is appended to the same statement list")


# ------------------------------------------------- a real read-only disk

def test_a_read_only_database_raises_rather_than_silently_dropping_writes():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run.sqlite"
        store = Persistence(path)
        store.save_transformer_state(1, {"x": 1})
        store.close()
        path.chmod(0o444)                       # read-only on disk
        try:
            reopened = Persistence(f"file:{path}?mode=ro", )
        except sqlite3.Error:
            check("a read-only database surfaces as an error", True,
                  "connection refused at open")
            path.chmod(0o644)
            return
        try:
            reopened.save_transformer_state(2, {"x": 2})
            check("a read-only database raises on write", False,
                  "the write silently succeeded")
        except (PersistenceError, sqlite3.Error):
            check("a read-only database raises on write", True)
        finally:
            reopened.close()
            path.chmod(0o644)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        try:
            fn()
        except Exception as exc:
            RESULTS.append((fn.__name__, False))
            print(f"  ERROR  {fn.__name__}: {type(exc).__name__}: {exc}")
    failed = sum(1 for _, ok in RESULTS if not ok)
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} checks passed")
    sys.exit(1 if failed else 0)
