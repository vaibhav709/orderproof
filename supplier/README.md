# Supplier catalog

OrderProof ships a read-only SQLite catalog, `demo.db`, containing fictional inventory snapshots. The deployment bundle must include this file. Order reviews, history and checkpoints use the separate order database.

For a private client installation, copy the three CSV files and retain their column headers. Product codes link the files. Stock is counted in sales units, pack size is pieces or kits per sales unit, and lead time is days. Preserve leading zeroes in serial suffixes. Use a distinct bulletin ID for each supplier bulletin. Build and validate the new catalog with:

    python3 catalog.py /path/to/client-csvs /path/to/client.db

Set ORDERPROOF_CATALOG_PATH to that file on the server, then restart or redeploy. Ship each catalog update as a new snapshot. No database credentials belong in the browser. The anonymous Vercel demo uses the bundled fictional catalog only. A client deployment needs private access controls before exposing its catalog. The order importer currently supports the supplied single-line mechanical seal kit PDF layout; other forms require an importer.

To rebuild the fictional data, run `python3 catalog.py supplier supplier/demo.db` from the repository root. The CSV files are the editable source. The Markdown bulletin is a readable copy of the original PX-40 example. The application uses ranges.csv through SQLite.
