import unittest
from pathlib import Path

from pypdf import PdfReader

from intake import IntakeError, parse_order_text, read_order

ORDERS = Path(__file__).resolve().parent / "orders"


class IntakeTests(unittest.TestCase):
    def test_serial_from_pdf_resolves_to_correct_kit(self):
        order = read_order(ORDERS / "PO-1001.pdf")
        self.assertEqual((order.order_id, order.line_number, order.model, order.quantity,
                          order.unit, order.serial, order.source_page, order.match.part_code),
                         ("PO-1001", 1, "PX-40", 2, "KIT", "18B042", 1, "SK-40B"))

    def test_missing_serial_from_pdf_requests_clarification(self):
        order = read_order(ORDERS / "PO-1002.pdf")
        self.assertEqual((order.order_id, order.quantity, order.serial), ("PO-1002", 2, ""))
        self.assertIsNone(order.match.part_code)
        self.assertIn("serial number or revision", order.match.reason)

    def test_unrecognized_content_requires_review(self):
        text = PdfReader(ORDERS / "PO-1001.pdf").pages[0].extract_text()
        for invalid in ["", text.replace("\n2\n", "\n0\n"),
                        text.replace("\nKIT\n", "\nBOX\n"),
                        text.replace("\nKIT\n", "\nKIT\n2\nSecond item\n1\nKIT\n")]:
            with self.subTest(invalid=invalid), self.assertRaises(IntakeError):
                parse_order_text(invalid)
