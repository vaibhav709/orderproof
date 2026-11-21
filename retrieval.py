"""Offline retrieval of fictional supplier records. Candidates still need review."""
import argparse
import math
import re
from collections import Counter
from dataclasses import dataclass

from catalog import rows
from matching import resolve_kit


@dataclass(frozen=True)
class Record:
    key: str
    text: str


def supplier_records():
    records = [Record("catalog/" + r["part_code"],
                      f'{r["part_code"]} {r["description"]}. Material: {r["material"]}. '
                      f'Stock: {r["stock_units"]} {r["sales_unit"]}. Units per sales unit: '
                      f'{r["kits_per_sales_unit"]}. Lead time: {r["lead_days"]} days. Bin: {r["bin_location"]}.')
               for r in rows("products")]
    mappings = {}
    for r in rows("compatibility"):
        mappings.setdefault(r["part_code"], []).append(
            f'{r["pump_model"]} revision {r["pump_revision"]} {r["part_role"]} {r["part_code"]}')
    records.extend(Record("parts/" + code, ". ".join(lines)) for code, lines in mappings.items())
    bulletins = {}
    for r in rows("serial_ranges"):
        bulletins.setdefault(r["bulletin"], []).append(
            f'Service bulletin {r["bulletin"]} for {r["pump_model"]}, prefix {r["prefix"]}: serial {r["prefix"]}{r["first_suffix"]} through '
            f'{r["prefix"]}{r["last_suffix"]}: revision {r["pump_revision"]}.')
    records.extend(Record("bulletin/" + code, " ".join(lines)) for code, lines in bulletins.items())
    return records


def tokens(text):
    words = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower())
    return [word.replace("-", "") for word in words]


def serial_evidence(query, records):
    words = set(re.findall(r"[A-Z0-9-]+", query.upper()))
    rules = rows("serial_ranges")
    candidates = [(word, r) for r in rules for word in words
                  if word.startswith(r["prefix"]) and word[len(r["prefix"]):].isdigit()
                  and len(word) == len(r["prefix"]) + len(r["first_suffix"])]
    if not candidates:
        return None
    by_key = {r.key: r for r in records}
    bulletin_keys = sorted({"bulletin/" + rule["bulletin"] for _, rule in candidates})
    evidence = [by_key[key] for key in bulletin_keys if key in by_key]
    serials = {word for word, _ in candidates}
    models = {rule["pump_model"] for _, rule in candidates}
    revisions = set(re.findall(r"\bREV(?:ISION)?[. :_-]+([A-Z0-9]+)\b", query.upper()))
    if len(serials) != 1 or len(models) != 1 or len(revisions) > 1:
        return evidence
    model = models.pop()
    # Recognize other identifiers in the same pump family, including unknown models.
    family = re.match(r"[A-Z]+", model)
    if family:
        mentioned = re.findall(r"\b" + family[0] + r"-?[0-9]+\b", query.upper())
        if any(item.replace("-", "") != model.replace("-", "") for item in mentioned):
            return evidence
    if any(r["pump_model"] != model and r["pump_model"].replace("-", "")
           in {w.replace("-", "") for w in words} for r in rules):
        return evidence
    match = resolve_kit(model, serials.pop(), next(iter(revisions), ""))
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
    # When the buyer names a known pump, rank evidence that covers that model.
    named_models = {tokens(r["pump_model"])[0] for r in rows("compatibility")} & words
    if named_models:
        records = [r for r in records if named_models.intersection(tokens(r.text))
                   or tokens(r.key.split("/")[-1])[0] in words]
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
