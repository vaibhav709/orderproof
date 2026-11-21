import os
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from urllib.parse import quote

from fastapi.testclient import TestClient

from api import create_app
from storage import configured_database


class StorageTests(unittest.TestCase):
    def test_hosted_app_requires_durable_storage(self):
        with patch.dict(os.environ, {"VERCEL": "1"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ORDERPROOF_DATABASE_URL"):
                configured_database()

    @unittest.skipUnless(os.getenv("ORDERPROOF_TEST_POSTGRES_URL"), "requires a PostgreSQL test database")
    def test_two_instances_share_one_review(self):
        import psycopg

        url = os.environ["ORDERPROOF_TEST_POSTGRES_URL"]
        schema = "orderproof_test_" + uuid4().hex
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f"CREATE SCHEMA {schema}")
            self.addCleanup(self.drop_schema, url, schema)
        # Isolate the test from any existing orders on the same server.
        separator = "&" if "?" in url else "?"
        url = url + separator + "options=" + quote(f"-c search_path={schema}")
        url_path = "/orders/po-1002"
        with TestClient(create_app(url)) as first:
            pdf = Path(__file__).with_name("orders").joinpath("PO-1002.pdf").read_bytes()
            self.assertEqual(first.post("/orders", files={"file": ("sample.pdf", pdf)}).status_code, 201)
        with TestClient(create_app(url)) as second:
            clarified = second.patch(url_path + "/clarification", json={"version": 1, "serial": "18B042"})
            self.assertEqual(clarified.json()["match"]["part_code"], "SK-40B")
            self.assertEqual(second.post(url_path + "/approval", json={"version": 2}).status_code, 200)
            self.assertEqual(len(second.get(url_path + "/history").json()), 3)
            self.assertIn("SK-40B,2,KIT", second.get(url_path + "/export").text)

    @staticmethod
    def drop_schema(url, schema):
        import psycopg
        with psycopg.connect(url) as db:
            db.execute(f"DROP SCHEMA {schema} CASCADE")
