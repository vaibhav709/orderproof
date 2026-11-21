from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from api import create_app


class DemoPrivacyTests(unittest.TestCase):
    def test_two_visitors_can_review_same_fixture_privately(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        app = create_app(Path(folder) / "demo.sqlite3", demo=True)
        a, b = [self.enterContext(TestClient(app, base_url="https://demo.test")) for _ in range(2)]
        raw = Path(__file__).with_name("orders").joinpath("PO-1002.pdf").read_bytes()
        def upload(client, pdf):
            return client.post("/orders", files={"file": ("order.pdf", pdf)})
        first = upload(a, raw)
        self.assertEqual(first.status_code, 201)
        self.assertIn("HttpOnly", first.headers["set-cookie"])
        url = "/orders/PO-1002"
        for suffix in ["", "/history", "/evidence?version=1", "/export"]:
            self.assertEqual(b.get(url + suffix).status_code, 404)
        self.assertEqual(b.post(url + "/approval", json={"version": 1}).status_code, 404)
        self.assertEqual(upload(b, raw + b"extra").status_code, 422)
        self.assertEqual(upload(b, raw).status_code, 201)
        change = {"version": 1, "serial": "18B042"}
        self.assertEqual(a.patch(url + "/clarification", json=change,
                                 headers={"Origin": "https://elsewhere.test"}).status_code, 403)
        self.assertEqual(a.patch(url + "/clarification", json=change).status_code, 200)
        self.assertEqual(a.post(url + "/approval", json={"version": 2}).status_code, 200)
        self.assertIn("SK-40B", a.get(url + "/export").text)
        self.assertEqual(len(b.get(url + "/export").text.splitlines()), 1)
        history = b.get(url + "/history")
        self.assertEqual(len(history.json()), 1)
        self.assertIn("no-store", history.headers["cache-control"])

    def test_px60_sample_requires_serial_and_approval_before_export(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        app = create_app(Path(folder) / "demo.sqlite3", demo=True)
        client = self.enterContext(TestClient(app, base_url="https://demo.test"))
        pdf = client.get("/samples/PO-1003")
        self.assertEqual(pdf.status_code, 200)
        draft = client.post("/orders", files={"file": ("sample.pdf", pdf.content)})
        self.assertEqual(draft.status_code, 201)
        self.assertEqual(draft.json()["status"], "needs_clarification")
        self.assertIsNone(draft.json()["match"]["part_code"])
        self.assertIn("serial number or revision", draft.json()["match"]["reason"])
        url = "/orders/PO-1003"
        self.assertEqual(client.post(url + "/approval", json={"version": 1}).status_code, 409)
        self.assertEqual(len(client.get(url + "/export").text.splitlines()), 1)
        self.assertEqual(client.patch(url + "/clarification",
                         json={"version": 1, "serial": "18B042"}).status_code, 422)
        revised = client.patch(url + "/clarification", json={"version": 1, "serial": "26T250"})
        self.assertEqual(revised.json()["match"]["part_code"], "SK-60B")
        self.assertEqual(revised.json()["status"], "awaiting_review")
        self.assertEqual(len(client.get(url + "/export").text.splitlines()), 1)
        self.assertEqual(client.post(url + "/approval", json={"version": 2}).status_code, 200)
        self.assertEqual(client.get(url + "/export").text.splitlines()[1:], ["PO-1003,1,SK-60B,2,KIT"])
