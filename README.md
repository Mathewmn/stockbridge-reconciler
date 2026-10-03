# Stockbridge Reconciler

A Python inventory ledger for an online shop: import deliveries, sales and returns, then compare the ledger against a physical stock count. Repeated imports must not double-count a sale, and concurrent orders must not oversell stock.

This is a portfolio exercise using synthetic camping-product data. It is not a deployed shop integration or a claim of commercial Python employment.

## Run the demo

Python 3.11 or newer. No third-party runtime packages required. Run from this directory, starting with a new database:

```sh
python stockbridge.py --db demo.sqlite3 apply demo_events.json
python stockbridge.py --db demo.sqlite3 apply demo_events.json
python stockbridge.py --db demo.sqlite3 inventory
python stockbridge.py --db demo.sqlite3 reconcile demo_count.csv
python stockbridge.py --db demo.sqlite3 audit
python -m unittest -v
```

The first import accepts five events. The second accepts zero and identifies five duplicates. The ledger contains 10 tents and 6 sleeping bags. The physical count reports 9 tents, so reconciliation returns a difference of -1. Reconciliation does not silently correct stock.

## Why these decisions

- **Idempotency:** an event ID is unique. Replaying its identical SKU and delta is a no-op; reusing the ID with a changed payload rejects the entire batch.
- **Atomicity:** stock updates and audit records commit together. One invalid or overselling event rolls back the whole ordered batch.
- **Concurrency:** `BEGIN IMMEDIATE` serializes SQLite writers before checking quantities. The tests race duplicate deliveries and competing sales through separate database connections.
- **Auditability:** each accepted event records its resulting quantity and a local sequence. Input order matters; this is not a distributed event ordering system.
- **Reconciliation:** the CSV represents a complete physical count; missing SKUs count as zero. The signed difference is counted minus ledger quantity.
- **Explicit failure:** invalid CSVs and rejected events return exit code 2; successful commands return 0.

The tests cover replay, conflicting payloads, transaction rollback, persistence, concurrent retries, overselling, CSV rejection, reconciliation and the real command-line process.

## Input contract

Each JSON event has exactly `event_id`, `sku` and `delta`. IDs and SKUs are case-sensitive, trimmed, non-empty strings of at most 120 characters. Delta is a nonzero integer between -1,000,000 and 1,000,000. Positive values receive stock; negative values consume it. Import batches are ordered, with 1–10,000 events.

CSV headers must be `sku,quantity`. Counts are nonnegative integers, and each SKU appears once. All queries use SQL parameters. Local database files are excluded from version control.

## Scope and next steps

SQLite suits a small local reconciliation tool and keeps the demo reproducible. There is no authentication, HTTP API, webhook signature verification, reservation lifecycle or live WooCommerce connection. Before integrating with a real shop, add authenticated adapters, source-specific event IDs, stock reservations, explicit schema migrations, backup procedures and operational monitoring. Move to a server database if multiple service instances need shared writes.

For an interview walkthrough, see [INTERVIEW_NOTES.md](INTERVIEW_NOTES.md). The source is intentionally small enough to read and explain in one sitting.
