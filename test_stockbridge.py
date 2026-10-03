import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from stockbridge import InventoryError, Ledger, read_snapshot


def event(key, delta, sku="TENT-2P"):
    return {"event_id": key, "sku": sku, "delta": delta}


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "inventory.db"
        self.ledger = Ledger(self.path)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_receipt_sale_and_return_leave_a_trace(self):
        self.ledger.apply([event("receipt", 10), event("sale", -3), event("return", 1)])
        self.assertEqual(self.ledger.inventory(), {"TENT-2P": 8})
        self.assertEqual([r["resulting_quantity"] for r in self.ledger.audit()], [10, 7, 8])

    def test_replaying_batch_is_idempotent(self):
        batch = [event("receipt", 10), event("sale", -3)]
        self.ledger.apply(batch)
        self.assertEqual(self.ledger.apply(batch), {"accepted": 0, "duplicates": 2})
        self.assertEqual(len(self.ledger.audit()), 2)
        self.assertEqual(self.ledger.inventory()["TENT-2P"], 7)

    def test_conflicting_key_rolls_back_entire_batch(self):
        self.ledger.apply([event("receipt", 10)])
        with self.assertRaises(InventoryError):
            self.ledger.apply([event("new", 5, "BAG"), event("receipt", 11)])
        self.assertEqual(self.ledger.inventory(), {"TENT-2P": 10})
        self.assertEqual(len(self.ledger.audit()), 1)

    def test_insufficient_stock_rolls_back_stock_and_audit(self):
        with self.assertRaises(InventoryError):
            self.ledger.apply([event("receipt", 2), event("sale", -3)])
        self.assertEqual(self.ledger.inventory(), {})
        self.assertEqual(self.ledger.audit(), [])

    def test_invalid_event_rolls_back_preceding_valid_event(self):
        for delta in [True, 1.5, 0, "2", 1000001]:
            with self.subTest(delta=delta), self.assertRaises(InventoryError):
                self.ledger.apply([event("valid", 2), event("invalid", delta)])
            self.assertEqual(self.ledger.inventory(), {})

    def test_persistence_across_connections(self):
        self.ledger.apply([event("receipt", 10)])
        other = Ledger(self.path)
        try:
            self.assertEqual(other.inventory(), {"TENT-2P": 10})
            self.assertEqual(other.apply([event("receipt", 10)])["duplicates"], 1)
        finally:
            other.close()

    def test_concurrent_retries_apply_once(self):
        def submit(_):
            ledger = Ledger(self.path)
            try:
                return ledger.apply([event("same-delivery", 10)])
            finally:
                ledger.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit, range(16)))
        self.assertEqual(sum(r["accepted"] for r in results), 1)
        self.assertEqual(sum(r["duplicates"] for r in results), 15)
        self.assertEqual(self.ledger.inventory(), {"TENT-2P": 10})

    def test_concurrent_sales_cannot_oversell(self):
        self.ledger.apply([event("receipt", 5)])
        def sell(i):
            ledger = Ledger(self.path)
            try:
                ledger.apply([event(f"sale-{i}", -1)])
                return True
            except InventoryError:
                return False
            finally:
                ledger.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(sell, range(12)))
        self.assertEqual(sum(results), 5)
        self.assertEqual(self.ledger.inventory()["TENT-2P"], 0)
        self.assertEqual(len(self.ledger.audit()), 6)

    def test_reconciliation_is_complete_and_does_not_mutate(self):
        self.ledger.apply([event("a", 5), event("b", 2, "BAG")])
        before = self.ledger.audit()
        self.assertEqual(self.ledger.reconcile({"TENT-2P": 4, "NEW": 3}), [
            {"sku": "BAG", "ledger": 2, "counted": 0, "difference": -2},
            {"sku": "NEW", "ledger": 0, "counted": 3, "difference": 3},
            {"sku": "TENT-2P", "ledger": 5, "counted": 4, "difference": -1},
        ])
        self.assertEqual(self.ledger.audit(), before)
        self.assertEqual(self.ledger.inventory(), {"BAG": 2, "TENT-2P": 5})

    def test_snapshot_rejects_duplicates_bad_columns_and_negative_counts(self):
        source = Path(self.temp.name) / "count.csv"
        for body in ["sku,quantity\nA,2\nA,3\n", "sku,quantity\nA,-1\n",
                     "quantity,sku\n2,A\n", "sku,quantity\nA,2,extra\n",
                     "sku,quantity\nA,1.5\n", "sku,quantity\nA\n"]:
            source.write_text(body)
            with self.subTest(body=body), self.assertRaises(InventoryError):
                read_snapshot(source)

    def test_cli_end_to_end_and_failed_import_exit_code(self):
        source = Path(self.temp.name) / "events.json"
        command = [sys.executable, str(Path(__file__).with_name("stockbridge.py")),
                   "--db", str(self.path)]
        source.write_text(json.dumps([event("receipt", 10)]))
        result = subprocess.run(command + ["apply", str(source)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["accepted"], 1)
        source.write_text(json.dumps([event("sale", -11)]))
        result = subprocess.run(command + ["apply", str(source)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Insufficient stock", result.stderr)
        result = subprocess.run(command + ["inventory"], capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout), {"TENT-2P": 10})


if __name__ == "__main__":
    unittest.main()
