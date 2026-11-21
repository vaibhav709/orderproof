"""Read the single-page Alder Channel Works demo order into a review draft."""
import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from matching import Match, resolve_kit


class IntakeError(ValueError):
    """The order cannot be read with the supported layout."""


@dataclass(frozen=True)
class OrderDraft:
    order_id: str
    line_number: int
    model: str
    quantity: int
    unit: str
    serial: str
    revision: str
    source_page: int
    match: Match


# Match the complete layout so an extra line item cannot disappear silently.
DEMO_LAYOUT = re.compile(
    r"ALDER CHANNEL WORKS\nPurchase order\n(?P<order_id>PO-[0-9]+)\n"
    r"SUPPLIER\nFenmere Pump Supply\nLINE\nITEM DESCRIPTION\nQTY\nUNIT\n"
    r"(?P<line>[1-9][0-9]*)\nMechanical seal kit for pump (?P<model>[A-Z0-9-]+)\n"
    r"(?P<quantity>[1-9][0-9]*)\nKIT\n"
    r"(?:Pump serial number: (?P<serial>[A-Z0-9-]+)\n)?"
    r"(?:Pump revision: (?P<revision>[A-Z0-9-]+)\n)?"
    r"Fictional purchase order for the OrderProof demo\n1 / 1",
    re.IGNORECASE,
)


def parse_order_text(text: str) -> OrderDraft:
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    row = DEMO_LAYOUT.fullmatch(normalized)
    if row is None:
        raise IntakeError("Unrecognized order layout or invalid line. Review the PDF manually.")
    serial = row["serial"] or ""
    revision = row["revision"] or ""
    match = resolve_kit(row["model"], serial, revision)
    return OrderDraft(row["order_id"], int(row["line"]), row["model"], int(row["quantity"]),
                      "KIT", serial, revision, 1, match)


def read_order_stream(source: BinaryIO) -> OrderDraft:
    try:
        pdf = PdfReader(source)
        if pdf.is_encrypted or len(pdf.pages) != 1:
            raise IntakeError("This importer requires one unencrypted PDF page.")
        text = pdf.pages[0].extract_text()
    except PdfReadError as exc:
        raise IntakeError("The PDF could not be read.") from exc
    return parse_order_text(text)


def read_order(path: Path) -> OrderDraft:
    with path.open("rb") as source:
        return read_order_stream(source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(asdict(read_order(args.pdf)), indent=2))
    except (IntakeError, OSError) as exc:
        parser.exit(1, f"{exc}\n")
