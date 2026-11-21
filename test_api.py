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

    def test_history_survives_restart_and_retries(self):
        url = "/orders/po-1002"
        self.assertEqual(self.client.get(url + "/history").status_code, 404)
        for _ in range(2):
            self.upload((ORDERS / "PO-1002.pdf").read_bytes())
        self.client.post(url + "/approval", json={"version": 1})
        self.client.patch(url + "/clarification", json={"version": 1, "serial": "18B042"})
        for _ in range(2):
            self.client.post(url + "/approval", json={"version": 2})
        with TestClient(create_app(self.database)) as restarted:
            events = restarted.get(url + "/history").json()
        self.assertEqual([(e["version"], e["action"]) for e in events],
                         [(1, "uploaded"), (2, "clarified"), (3, "approved")])
        self.assertEqual(events[0]["serial"], "")
        self.assertEqual(events[1]["serial"], "18B042")
        self.assertEqual(events[1]["match"]["part_code"], "SK-40B")
        self.assertEqual(events[2]["status"], "approved")

    def test_legacy_order_history_begins_at_current_version(self):
        import sqlite3
        self.upload((ORDERS / "PO-1001.pdf").read_bytes())
        self.client.post("/orders/PO-1001/approval", json={"version": 1})
        with sqlite3.connect(self.database) as db:
            db.execute("DROP TABLE review_history")
        with TestClient(create_app(self.database)) as restarted:
            events = restarted.get("/orders/PO-1001/history").json()
        self.assertEqual([(e["action"], e["version"], e["status"]) for e in events],
                         [("existing_draft", 2, "approved")])

    def test_resume_after_restart_at_each_human_pause(self):
        import sqlite3
        url = "/orders/PO-1002"
        self.upload((ORDERS / "PO-1002.pdf").read_bytes())
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM review_checkpoints").fetchone(), (1,))
        with TestClient(create_app(self.database)) as restarted:
            result = restarted.patch(url + "/clarification", json={"version": 1, "serial": "18B042"})
            self.assertEqual(result.json()["match"]["part_code"], "SK-40B")
            self.assertEqual(len(restarted.get(url + "/export").text.splitlines()), 1)
        with TestClient(create_app(self.database)) as restarted:
            self.assertEqual(restarted.post(url + "/approval", json={"version": 2}).json()["version"], 3)
            self.assertIn("SK-40B,2,KIT", restarted.get(url + "/export").text)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM review_checkpoints").fetchone(), (0,))

    def test_existing_waiting_order_without_checkpoint_can_resume(self):
        import sqlite3
        self.upload((ORDERS / "PO-1001.pdf").read_bytes())
        with sqlite3.connect(self.database) as db:
            db.execute("DELETE FROM review_checkpoints")
        result = self.client.post("/orders/PO-1001/approval", json={"version": 1})
        self.assertEqual(result.json()["status"], "approved")

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

    def test_live_evidence_tracks_draft_version(self):
        for name in ("PO-1001", "PO-1002"):
            self.upload((ORDERS / f"{name}.pdf").read_bytes())
        def keys(name, version):
            result = self.client.get(f"/orders/{name}/evidence", params={"version": version})
            self.assertEqual(result.status_code, 200)
            return [record["key"] for record in result.json()]
        self.assertEqual(keys("PO-1001", 1), ["bulletin/SB-040", "parts/SK-40B", "catalog/SK-40B"])
        self.assertTrue(keys("PO-1002", 1))
        self.client.patch("/orders/PO-1002/clarification", json={"version": 1, "serial": "18B015"})
        self.assertEqual(self.client.get("/orders/PO-1002/evidence?version=1").status_code, 409)
        self.assertEqual(keys("PO-1002", 2), ["bulletin/SB-040", "parts/SK-40A", "catalog/SK-40A"])
        self.assertEqual(self.client.get("/orders/PO-1002").json()["status"], "awaiting_review")
