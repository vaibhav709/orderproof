import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from api import create_app

ORDERS = Path(__file__).with_name("orders")


class UploadTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.database = Path(folder) / "orders.sqlite3"
        self.client = self.enterContext(TestClient(create_app(self.database)))

    def upload(self, content, name="buyer.pdf"):
        return self.client.post("/orders", files={"file": (name, content, "application/pdf")})

    def test_persistence_and_conflicting_revision(self):
        content = (ORDERS / "PO-1001.pdf").read_bytes()
        first = self.upload(content)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["status"], "awaiting_review")
        duplicate = self.upload(content, "renamed.pdf")
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.json(), first.json())
        self.assertEqual(self.upload(content + b"\n").status_code, 409)
        with TestClient(create_app(self.database)) as restarted:
            self.assertEqual(restarted.get("/orders/PO-1001").json(), first.json())

    def test_concurrent_retries_create_one_draft(self):
        content = (ORDERS / "PO-1002.pdf").read_bytes()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(self.upload, [content] * 4))
        self.assertEqual(sorted(r.status_code for r in results), [200, 200, 200, 201])
        self.assertTrue(all(r.json()["status"] == "needs_clarification" for r in results))

    def test_invalid_upload_and_unknown_order(self):
        self.assertEqual(self.upload(b"not a PDF").status_code, 422)
        self.assertEqual(self.upload(b"%PDF-broken").status_code, 422)
        self.assertEqual(self.client.get("/orders/missing").status_code, 404)

    def test_clarification_then_approval(self):
        self.upload((ORDERS / "PO-1002.pdf").read_bytes())
        url = "/orders/PO-1002"
        def approve(version):
            return self.client.post(url + "/approval", json={"version": version})
        self.assertEqual(approve(1).status_code, 409)
        result = self.client.patch(url + "/clarification", json={"version": 1, "serial": "18B042"})
        self.assertEqual(result.status_code, 200)
        draft = result.json()
        self.assertEqual(draft["original_evidence"]["serial"], "")
        self.assertEqual(draft["match"]["part_code"], "SK-40B")
        self.assertEqual(draft["status"], "awaiting_review")
        self.assertEqual(approve(1).status_code, 409)
        approved = approve(draft["version"]).json()
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approve(draft["version"]).json(), approved)
        self.assertEqual(self.client.get(url).json(), approved)
        self.assertEqual(self.client.patch(url + "/clarification",
                         json={"version": approved["version"], "revision": "A"}).status_code, 409)

    def test_conflicting_revision_blocks_approval(self):
        self.upload((ORDERS / "PO-1001.pdf").read_bytes())
        url = "/orders/PO-1001"
        draft = self.client.patch(url + "/clarification", json={"version": 1, "revision": "A"}).json()
        self.assertEqual(draft["status"], "needs_clarification")
        self.assertEqual(draft["serial"], "18B042")
        self.assertEqual(self.client.post(url + "/approval", json={"version": 2}).status_code, 409)

    def test_approved_export_matches_reference_and_retries(self):
        for order in ["PO-1001", "PO-1002"]:
            self.upload((ORDERS / f"{order}.pdf").read_bytes())
            url = f"/orders/{order}/export"
            self.assertEqual(len(self.client.get(url).text.splitlines()), 1)
        self.client.post("/orders/PO-1001/approval", json={"version": 1})
        expected = (ORDERS.parent / "expected" / "approved_orders.csv").read_text()
        for attempt in range(2):
            self.assertEqual(self.client.get("/orders/PO-1001/export").text, expected)
