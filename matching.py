"""Resolve a requested pump part against the configured supplier catalog."""
from dataclasses import dataclass

from catalog import rows


@dataclass(frozen=True)
class Match:
    part_code: str | None
    reason: str


def resolve_kit(model: str, serial: str = "", revision: str = "") -> Match:
    model, serial, revision = (value.strip().upper() for value in (model, serial, revision))
    parts = [row for row in rows("compatibility")
             if row["pump_model"] == model and row["part_role"] == "mechanical seal kit"]
    if not parts:
        return Match(None, "No supplier mapping for this model and part type.")
    evidence = "Buyer supplied the pump revision."
    if serial:
        hits = [r for r in rows("serial_ranges") if r["pump_model"] == model
                and serial.startswith(r["prefix"])
                and len(serial) == len(r["prefix"]) + len(r["first_suffix"])
                and serial[len(r["prefix"]):].isascii() and serial[len(r["prefix"]):].isdigit()
                and r["prefix"] + r["first_suffix"] <= serial <= r["prefix"] + r["last_suffix"]]
        if len(hits) != 1:
            return Match(None, "Ask the supplier to confirm this serial.")
        rule = hits[0]
        inferred = rule["pump_revision"]
        first = rule["prefix"] + rule["first_suffix"]
        last = rule["prefix"] + rule["last_suffix"]
        if revision and revision != inferred:
            return Match(None, "The supplied revision conflicts with the serial bulletin.")
        revision = inferred
        evidence = f"{rule['bulletin']} places {serial} in {first}–{last}, revision {revision}."
    if not revision:
        return Match(None, "Ask the buyer for the pump serial number or revision.")
    codes = {row["part_code"] for row in parts if row["pump_revision"] == revision}
    if len(codes) != 1:
        return Match(None, "No unique compatible part for this revision.")
    code = codes.pop()
    if not any(row["part_code"] == code for row in rows("products")):
        return Match(None, "The compatible part is missing from the catalog.")
    return Match(code, f"{evidence} The parts table maps {model} revision {revision} to {code}.")
