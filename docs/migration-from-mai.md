# Migration From Mai

Use:

```bash
python3 scripts/mai.py --db ./mai-cli.sqlite legacy import --from-json ./mai.json --format json
```

The legacy adapter imports:

- merchants
- products
- public tags and catalog fields
- product stock

It intentionally ignores legacy transaction records and payment-like records because the mai-cli MVP is consultation-only. After import, configure delivery rules with `merchant create` fields or `delivery set`.

The import can be retried safely. Existing merchants are skipped by merchant id, existing products are skipped by sku, and the command reports imported and skipped counts instead of creating duplicates.
