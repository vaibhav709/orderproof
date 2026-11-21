"""Offline retrieval of fictional supplier records. Candidates still need review."""
import argparse
import math
import re
from collections import Counter
from dataclasses import dataclass

from matching import SUPPLIER, read_rows, resolve_kit


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


def serial_evidence(query, records):
    """Rank evidence from the demo bulletin when a buyer quotes an 18B serial."""
    serials = set(re.findall(r"\b18B[0-9]{3}\b", query.upper()))
    if not serials:
        return None
    by_key = {r.key: r for r in records}
    bulletin = by_key.get("bulletin/SB-040")
    if bulletin is None:
        return []
    evidence = [bulletin]
    models = set(re.findall(r"\bPX-?([0-9]+)\b", query.upper()))
    revisions = set(re.findall(r"\bREV(?:ISION)?[. :_-]+([A-Z0-9]+)\b", query.upper()))
    if len(serials) != 1 or models - {"40"} or len(revisions) > 1:
        return evidence
    match = resolve_kit("PX-40", serials.pop(), next(iter(revisions), ""))
    if match.part_code:
        for key in ["parts/" + match.part_code, "catalog/" + match.part_code]:
            if key in by_key:
                evidence.append(by_key[key])
    return evidence


def shortlist(query, records, method="serial-aware", limit=3):
    if method == "serial-aware":
        evidence = serial_evidence(query, records)
        if evidence is not None:
            return evidence[:limit]
        method = "lexical"
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
