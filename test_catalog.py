import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from catalog import DEFAULT, build
from matching import resolve_kit
from retrieval import shortlist, supplier_records
from api import create_app
from fastapi.testclient import TestClient


class CatalogTests(unittest.TestCase):
    def test_replacement_pack_changes_lookup_search_and_display(self):
        with tempfile.TemporaryDirectory() as work:
            folder = Path(work)
            for source in DEFAULT.parent.glob("*.csv"):
                shutil.copy(source, folder)
            for name in ["catalog.csv", "parts.csv"]:
                path = folder / name
                path.write_text(path.read_text().replace("SK-60B", "CLIENT-60"))
            db = folder / "client.db"
            build(folder, db)
            with patch.dict(os.environ, {"ORDERPROOF_CATALOG_PATH": str(db)}):
                self.assertEqual(resolve_kit("PX-60", "26T250").part_code, "CLIENT-60")
                self.assertIn("parts/CLIENT-60", [r.key for r in shortlist("26T250", supplier_records())])
                with TestClient(create_app(folder / "orders.db")) as client:
                    products = client.get("/catalog").json()["products"]
                    self.assertTrue(any(p["part_code"] == "CLIENT-60" and p["stock_units"] == 0 for p in products))
                with self.assertRaises(RuntimeError):
                    create_app(folder / "orders.db", demo=True)
            (folder / "ranges.csv").write_text((folder / "ranges.csv").read_text()
                                               + "SB-060,PX-60,26T,248,260,B\n")
            with self.assertRaises(ValueError):
                build(folder, db)
            with patch.dict(os.environ, {"ORDERPROOF_CATALOG_PATH": str(db)}):
                self.assertEqual(resolve_kit("PX-60", "26T250").part_code, "CLIENT-60")
