"""Build a portable catalog: python3 catalog.py SOURCE_DIRECTORY OUTPUT.db"""
import argparse
import csv
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent / "supplier" / "demo.db"
FILES = {"products": "catalog.csv", "compatibility": "parts.csv", "serial_ranges": "ranges.csv"}
SCHEMA = """
CREATE TABLE products(part_code TEXT PRIMARY KEY, description TEXT NOT NULL,
 sales_unit TEXT NOT NULL, kits_per_sales_unit INTEGER NOT NULL CHECK(kits_per_sales_unit>0),
 data_origin TEXT NOT NULL, material TEXT NOT NULL, stock_units INTEGER NOT NULL CHECK(stock_units>=0),
 lead_days INTEGER NOT NULL CHECK(lead_days>=0), bin_location TEXT NOT NULL) STRICT;
CREATE TABLE compatibility(pump_model TEXT, pump_revision TEXT, part_role TEXT,
 part_code TEXT NOT NULL REFERENCES products, data_origin TEXT NOT NULL,
 PRIMARY KEY(pump_model,pump_revision,part_role,part_code)) STRICT;
CREATE TABLE serial_ranges(bulletin TEXT, pump_model TEXT, prefix TEXT, first_suffix TEXT,
 last_suffix TEXT NOT NULL, pump_revision TEXT NOT NULL,
 PRIMARY KEY(pump_model,prefix,first_suffix),
 CHECK(length(first_suffix)>0 AND length(first_suffix)=length(last_suffix)
 AND first_suffix NOT GLOB '*[^0-9]*' AND last_suffix NOT GLOB '*[^0-9]*'
 AND first_suffix<=last_suffix)) STRICT;
"""


def build(source, output):
    with tempfile.TemporaryDirectory(dir=output.parent) as folder:
        temp = Path(folder) / "catalog.db"
        with closing(sqlite3.connect(temp)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript(SCHEMA)
            for table, filename in FILES.items():
                columns = [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
                with (source / filename).open(newline="", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    if set(reader.fieldnames or []) != set(columns):
                        raise ValueError(f"Unexpected columns in {filename}")
                    db.executemany(f"INSERT INTO {table} VALUES ({','.join('?' for _ in columns)})",
                                   ([row[c] for c in columns] for row in reader))
            for model, prefix, start, end, revision in db.execute(
                    "SELECT pump_model,prefix,first_suffix,last_suffix,pump_revision FROM serial_ranges"):
                overlaps = db.execute("""SELECT count(*) FROM serial_ranges WHERE pump_model=? AND prefix=?
                    AND length(first_suffix)=length(?) AND first_suffix<=? AND last_suffix>=?""",
                                      (model, prefix, start, end, start)).fetchone()[0]
                mapped = db.execute("SELECT 1 FROM compatibility WHERE pump_model=? AND pump_revision=?",
                                    (model, revision)).fetchone()
                if overlaps != 1 or not mapped:
                    raise ValueError(f"Invalid serial mapping for {model} {prefix}{start}")
        os.replace(temp, output)


def rows(table):
    if table not in FILES:
        raise ValueError("Unknown catalog table")
    path = Path(os.getenv("ORDERPROOF_CATALOG_PATH") or DEFAULT).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(f"SELECT * FROM {table}")]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.source, args.output)
