"""Resolve a requested pump part against the fictional PX-40 supplier records."""
import csv
import re
from dataclasses import dataclass
from pathlib import Path

SUPPLIER = Path(__file__).resolve().parent / "supplier"


@dataclass(frozen=True)
class Match:
    part_code: str | None
    reason: str


def read_rows(filename):
    with (SUPPLIER / filename).open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def resolve_kit(model: str, serial: str = "", revision: str = "") -> Match:
    model, serial, revision = (value.strip().upper() for value in (model, serial, revision))
    parts = [row for row in read_rows("parts.csv")
             if row["pump_model"] == model and row["part_role"] == "mechanical seal kit"]
    if not parts:
        return Match(None, "No supplier mapping for this model and part type.")
    evidence = "Buyer supplied the pump revision."
    if serial:
        bulletin = (SUPPLIER / "bulletin.md").read_text(encoding="utf-8")
        if f"Applies only to the {model} pump." not in bulletin:
            return Match(None, "The serial bulletin does not cover this model.")
        ranges = re.findall(r"^\| (\w+) \| (\w+) \| (\w+) \|$", bulletin, re.MULTILINE)
        hits = [(first, last, rev) for first, last, rev in ranges
                if re.fullmatch(r"18B[0-9]{3}", serial) and first <= serial <= last]
        if len(hits) != 1:
            return Match(None, "Ask the supplier to confirm this serial.")
        first, last, inferred = hits[0]
        if revision and revision != inferred:
            return Match(None, "The supplied revision conflicts with the serial bulletin.")
        revision = inferred
        evidence = f"SB-040 places {serial} in {first}–{last}, revision {revision}."
    if not revision:
        return Match(None, "Ask the buyer for the pump serial number or revision.")
    codes = {row["part_code"] for row in parts if row["pump_revision"] == revision}
    if len(codes) != 1:
        return Match(None, "No unique compatible part for this revision.")
    code = codes.pop()
    if not any(row["part_code"] == code for row in read_rows("catalog.csv")):
        return Match(None, "The compatible part is missing from the catalog.")
    return Match(code, f"{evidence} The parts table maps {model} revision {revision} to {code}.")
