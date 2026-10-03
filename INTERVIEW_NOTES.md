# Walkthrough and exercises

Use these notes to understand the implementation. Do not present generated wording as proof of understanding or describe unimplemented features as finished work.

## A five-minute demonstration

1. Import `demo_events.json`; explain deliveries, sales and returns.
2. Import it again. Show the duplicate count and unchanged stock.
3. Change a delta while keeping the same event ID. Explain why a changed payload is a conflict rather than a retry.
4. Try a sale greater than available stock. Show that both stock and audit records remain unchanged.
5. Run the count reconciliation and explain why the tool reports differences without applying corrections.
6. Run the concurrent sales test and trace the database transaction boundary.

## Questions to answer in your own words

- Why does checking stock before starting a transaction allow overselling?
- Why must the audit insert be in the same transaction as the stock update?
- What happens if the process crashes after COMMIT but before printing success?
- Why is a local sequence not proof of the real-world event order?
- What does a missing SKU mean in this CSV contract? How would a partial count change the interface?
- How would you distinguish a returned item from cancellation of a reserved order?
- When would PostgreSQL be a better fit than SQLite?

## Small extensions to implement yourself

- Add an explicit adjustment command requiring a reason and a separate audit event.
- Add tests for two events with the same ID inside one import batch.
- Add a report filtered by SKU without changing the write transaction.

Make changes, run the tests, and explain the tradeoffs before adding this project as interview evidence.
