"""Run with python benchmark.py. Labels are read only by the evaluator."""
import csv
from pathlib import Path

from retrieval import shortlist, supplier_records


def evaluate(cases, records, method):
    positive = complete = negative = empty = 0
    failures = []
    keys = {r.key for r in records}
    for case in cases:
        required = set(case["required"].split())
        if not required <= keys:
            raise ValueError(f"Unknown evidence in {case}")
        found = {r.key for r in shortlist(case["query"], records, method)}
        if required:
            positive += 1
            complete += required <= found
            if required - found:
                failures.append(case["query"])
        else:
            negative += 1
            empty += not found
    return f"all required records in top 3: {complete}/{positive}; empty on unrelated lines: {empty}/{negative}", failures


if __name__ == "__main__":
    path = Path(__file__).parent / "evaluation" / "retrieval.csv"
    with path.open(newline="") as source:
        cases = list(csv.DictReader(source))
    records = supplier_records()
    for split in ["development", "evaluation"]:
        for method in ["exact", "lexical", "serial-aware"]:
            result, misses = evaluate([c for c in cases if c["split"] == split], records, method)
            print(split, method, result)
            if misses:
                print("  Example miss:", misses[0])
