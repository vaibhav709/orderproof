"""Offline retrieval of fictional supplier records. Candidates still need review."""
import argparse
import math
import re
from collections import Counter
from dataclasses import dataclass

from matching import SUPPLIER, read_rows


@dataclass(frozen=True)
class Record:
    key: str
    text: str


def supplier_records():
    records = [Record("catalog/" + row["part_code"], f'{row["part_code"]} {row["description"]}')
               for row in read_rows("catalog.csv")]
    for row in read_rows("parts.csv"):
        records.append(Record("parts/" + row["part_code"],
                              f'{row["pump_model"]} revision {row["pump_revision"]} '
                              f'{row["part_role"]} {row["part_code"]}'))
    records.append(Record("bulletin/SB-040", (SUPPLIER / "bulletin.md").read_text()))
    return records


def tokens(text):
    words = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower())
    return [word.replace("-", "") for word in words]


def shortlist(query, records, method="lexical", limit=3):
    words = set(tokens(query))
    if method == "exact":
        hits = [r for r in records if tokens(r.key.split("/")[1])[0] in words]
        return hits[:limit]
    if method != "lexical":
        raise ValueError("Unknown retrieval method")
    counts = [Counter(tokens(r.text)) for r in records]
    ranking = []
    for record, terms in zip(records, counts):
        score = 0.0
        for word in sorted(words.intersection(terms)):
            frequency = sum(word in other for other in counts)
            weight = math.log((len(records) + 1) / frequency)
            score += weight * (1 + math.log(terms[word]))
        if score:
            ranking.append((score, record))
    ranking.sort(key=lambda item: (-item[0], item[1].key))
    return [record for _, record in ranking[:limit]]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    for result in shortlist(parser.parse_args().query, supplier_records()):
        print(result.key, result.text, sep="\n")
