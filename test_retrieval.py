import unittest

from retrieval import shortlist, supplier_records


class SerialRetrievalTests(unittest.TestCase):
    def keys(self, query):
        return [r.key for r in shortlist(query, supplier_records())]

    def test_boundary_and_case(self):
        for serial, kit in [("18b039", "SK-40A"), ("18B040", "SK-40B")]:
            self.assertEqual(self.keys("Pump plate " + serial),
                             ["bulletin/SB-040", "parts/" + kit, "catalog/" + kit])

    def test_unresolved_evidence(self):
        for query in ["18B100", "18B042 revision A", "18B015 and 18B042", "PX99 serial 18B042"]:
            self.assertEqual(self.keys(query), ["bulletin/SB-040"])

    def test_no_serial_uses_existing_ranking(self):
        records = supplier_records()
        self.assertEqual(shortlist("PX40 seal kit", records),
                         shortlist("PX40 seal kit", records, method="lexical"))
