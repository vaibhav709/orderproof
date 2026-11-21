import unittest
from collections import Counter

from model_benchmark import score
from retrieval import Record


class BenchmarkTests(unittest.TestCase):
    def test_serial_answer_needs_both_sources(self):
        case = {"decision": "SK-40B", "required": "bulletin/SB-040 parts/SK-40B"}
        evidence = [Record(key, "") for key in case["required"].split()]
        counts = Counter()
        for citations in [[], ["invented"], case["required"].split()]:
            score(case, {"decision": "SK-40B", "citations": citations}, evidence, counts)
        self.assertEqual(counts, {"correct_with_evidence": 1, "unknown_citations": 1})

    def test_guess_on_ambiguous_order_counts_as_wrong(self):
        counts = Counter()
        score({"decision": "review"}, {"decision": "SK-40A", "citations": []}, [], counts)
        self.assertEqual(counts["wrong_part_suggestions"], 1)

    def test_quoted_request_is_invalid_even_with_correct_citation(self):
        counts = Counter()
        key = "catalog/SK-40A"
        score({"decision": "SK-40A", "required": key},
              {"decision": "Quote SK-40A", "citations": [key]}, [Record(key, "")], counts)
        self.assertEqual(counts, {"invalid_output": 1})

    def test_bearing_and_gasket_count_as_catalog_decisions(self):
        counts = Counter()
        score({"decision": "SK-40A"}, {"decision": "BR-40", "citations": ["parts/BR-40"]},
              [Record("parts/BR-40", "")], counts)
        score({"decision": "GS-60"}, {"decision": "review", "citations": []}, [], counts)
        self.assertEqual(counts, {"wrong_part_suggestions": 1, "unnecessary_reviews": 1})
