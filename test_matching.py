import unittest

from matching import resolve_kit


class CompatibilityTests(unittest.TestCase):
    def test_documented_ranges(self):
        for suffix in range(1000):
            serial = f"18B{suffix:03}"
            expected = "SK-40A" if suffix < 40 else "SK-40B" if suffix < 100 else None
            self.assertEqual(resolve_kit("PX-40", serial).part_code, expected, serial)

    def test_review_when_evidence_is_missing_invalid_or_conflicting(self):
        for model, serial, revision in [
            ("PX-40", "", ""), ("PX-40", "18B042", "A"), ("PX-40", "18B42", ""),
            ("PX-99", "18B042", ""), ("PX-40", "", "Z"),
        ]:
            self.assertIsNone(resolve_kit(model, serial, revision).part_code)

    def test_explicit_revision_and_normalization(self):
        self.assertEqual(resolve_kit("px-40", revision=" b ").part_code, "SK-40B")
