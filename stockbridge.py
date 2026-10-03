"""A small SQLite inventory ledger and a read-only CSV reconciliation command."""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path


class InventoryError(ValueError):
    """A rejected business operation; the transaction must not be committed."""


def identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 120:
        raise InventoryError(f"{name} must be a non-empty string of at most 120 characters")
    return value.strip()


class Ledger:
    def __init__(self, database: str | Path):
        self.db = sqlite3.connect(database, timeout=10, isolation_level=None)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS stock (
                sku TEXT PRIMARY KEY, quantity INTEGER NOT NULL CHECK(quantity >= 0)
            );
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                sku TEXT NOT NULL REFERENCES stock(sku),
                delta INTEGER NOT NULL CHECK(delta != 0),
                resulting_quantity INTEGER NOT NULL CHECK(resulting_quantity >= 0)
            );
        """)

    def close(self):
        self.db.close()

    def apply(self, events: list[dict]) -> dict:
        """Apply a whole ordered batch or nothing, including its audit records."""
        if not isinstance(events, list) or not events or len(events) > 10000:
            raise InventoryError("Provide between 1 and 10000 events")
        accepted = duplicates = 0
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for event in events:
                if not isinstance(event, dict) or set(event) != {"event_id", "sku", "delta"}:
                    raise InventoryError("Each event requires exactly event_id, sku and delta")
                event_id = identifier(event["event_id"], "event_id")
                sku = identifier(event["sku"], "sku")
                delta = event["delta"]
                if type(delta) is not int or delta == 0 or abs(delta) > 1000000:
                    raise InventoryError("delta must be a nonzero integer within +/-1000000")
                previous = self.db.execute(
                    "SELECT sku, delta FROM events WHERE event_id=?", (event_id,)
                ).fetchone()
                if previous:
                    if previous != (sku, delta):
                        raise InventoryError(f"Conflicting payload for event {event_id}")
                    duplicates += 1
                    continue
                self.db.execute("INSERT OR IGNORE INTO stock VALUES (?,0)", (sku,))
                quantity = self.db.execute(
                    "SELECT quantity FROM stock WHERE sku=?", (sku,)
                ).fetchone()[0] + delta
                if quantity < 0:
                    raise InventoryError(f"Insufficient stock for {sku}")
                self.db.execute("UPDATE stock SET quantity=? WHERE sku=?", (quantity, sku))
                self.db.execute(
                    "INSERT INTO events(event_id,sku,delta,resulting_quantity) VALUES (?,?,?,?)",
                    (event_id, sku, delta, quantity),
                )
                accepted += 1
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return {"accepted": accepted, "duplicates": duplicates}

    def inventory(self) -> dict[str, int]:
        return dict(self.db.execute("SELECT sku,quantity FROM stock ORDER BY sku"))

    def audit(self) -> list[dict]:
        columns = ("sequence", "event_id", "sku", "delta", "resulting_quantity")
        return [dict(zip(columns, row)) for row in self.db.execute(
            "SELECT sequence,event_id,sku,delta,resulting_quantity FROM events ORDER BY sequence"
        )]

    def reconcile(self, snapshot: dict[str, int]) -> list[dict]:
        """Snapshot is a complete physical count. A missing SKU counts as zero."""
        stock = self.inventory()
        return [
            {"sku": sku, "ledger": stock.get(sku, 0), "counted": snapshot.get(sku, 0),
             "difference": snapshot.get(sku, 0) - stock.get(sku, 0)}
            for sku in sorted(stock.keys() | snapshot.keys())
            if stock.get(sku, 0) != snapshot.get(sku, 0)
        ]


def read_snapshot(path: Path) -> dict[str, int]:
    snapshot = {}
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != ["sku", "quantity"]:
            raise InventoryError("CSV headers must be sku,quantity, in that order")
        for number, row in enumerate(reader, 2):
            if set(row) != {"sku", "quantity"} or row["quantity"] is None:
                raise InventoryError(f"Invalid CSV row {number}")
            sku = identifier(row["sku"], "sku")
            try:
                quantity = int(row["quantity"])
            except (ValueError, TypeError) as error:
                raise InventoryError(f"Invalid quantity at row {number}") from error
            if quantity < 0 or sku in snapshot:
                raise InventoryError(f"Negative quantity or duplicate SKU at row {number}")
            snapshot[sku] = quantity
    return snapshot


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("inventory.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inventory")
    commands.add_parser("audit")
    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("events", type=Path)
    reconcile_parser = commands.add_parser("reconcile")
    reconcile_parser.add_argument("snapshot", type=Path)
    args = parser.parse_args(argv)
    ledger = None
    try:
        # Validate source before creating a database for malformed input.
        data = None
        if args.command == "apply":
            data = json.loads(args.events.read_text(encoding="utf-8"))
        elif args.command == "reconcile":
            data = read_snapshot(args.snapshot)
        ledger = Ledger(args.db)
        if args.command == "apply":
            result = ledger.apply(data)
        elif args.command == "reconcile":
            result = ledger.reconcile(data)
        else:
            result = getattr(ledger, args.command)()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (InventoryError, OSError, sqlite3.Error, json.JSONDecodeError) as error:
        print(f"Rejected: {error}", file=sys.stderr)
        return 2
    finally:
        if ledger:
            ledger.close()


if __name__ == "__main__":
    sys.exit(main())
