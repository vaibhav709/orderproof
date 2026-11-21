"""Local single-buyer order intake. Run with uvicorn api:app."""
import csv
import hashlib
import json
import sqlite3
from contextlib import asynccontextmanager, closing
from dataclasses import asdict
from io import BytesIO, StringIO
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, UploadFile

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, ConfigDict, Field

from intake import IntakeError, read_order_stream
from matching import resolve_kit, read_rows
from retrieval import shortlist, supplier_records

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class ReviewVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class Clarification(ReviewVersion):
    serial: str | None = Field(default=None, max_length=64)
    revision: str | None = Field(default=None, max_length=32)


def load_order(db, order_id):
    row = db.execute("SELECT document FROM drafts WHERE order_id = ?", (order_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Order not found.")
    document = json.loads(row[0])
    document.setdefault("version", 1)  # Drafts saved before review versioning.
    return document


def record_event(db, document, action):
    state = {key: document[key] for key in ("status", "serial", "revision", "match")}
    db.execute("""INSERT INTO review_history (order_id, version, action, state)
                  VALUES (?, ?, ?, ?)""",
               (document["order_id"], document["version"], action, json.dumps(state)))


def save_review(db, document, action):
    document["version"] += 1
    db.execute("UPDATE drafts SET document = ? WHERE order_id = ?",
               (json.dumps(document), document["order_id"]))
    record_event(db, document, action)
    return document


def check_version(document, supplied):
    if document["version"] != supplied:
        raise HTTPException(409, "The draft changed. Reload before reviewing it.")


def create_app(database: Path) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS drafts (
                order_id TEXT PRIMARY KEY COLLATE NOCASE,
                fingerprint TEXT NOT NULL UNIQUE,
                document TEXT NOT NULL
            )""")
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS review_history (
                order_id TEXT COLLATE NOCASE NOT NULL,
                version INTEGER NOT NULL,
                action TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                state TEXT NOT NULL,
                PRIMARY KEY (order_id, version)
            )""")
            for order_id, in db.execute("""SELECT order_id FROM drafts d WHERE NOT EXISTS
                    (SELECT 1 FROM review_history h WHERE h.order_id = d.order_id)"""):
                record_event(db, load_order(db, order_id), "existing_draft")
        yield

    app = FastAPI(title="OrderProof", lifespan=lifespan)

    @app.post("/orders", status_code=201)
    def upload(file: UploadFile, response: Response):
        content = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "The PDF must be 5 MiB or smaller.")
        if not content.startswith(b"%PDF-"):
            raise HTTPException(422, "Select a text-based PDF purchase order.")
        fingerprint = hashlib.sha256(content).hexdigest()
        with closing(sqlite3.connect(database)) as db, db:
            def previous_upload():
                return db.execute("SELECT document FROM drafts WHERE fingerprint = ?",
                                  (fingerprint,)).fetchone()

            existing = previous_upload()
            if existing:
                response.status_code = 200
                return {"version": 1, **json.loads(existing[0])}
            try:
                draft = read_order_stream(BytesIO(content))
            except IntakeError as exc:
                raise HTTPException(422, str(exc)) from exc
            result = asdict(draft)
            result["version"] = 1
            result["status"] = "awaiting_review" if draft.match.part_code else "needs_clarification"
            inserted = db.execute(
                "INSERT INTO drafts VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                (draft.order_id, fingerprint, json.dumps(result)),
            )
            if inserted.rowcount:
                record_event(db, result, "uploaded")
                return result
            # Another request may have inserted the same upload while we parsed it.
            existing = previous_upload()
            if existing:
                response.status_code = 200
                return {"version": 1, **json.loads(existing[0])}
            raise HTTPException(409, "This order number already has a different PDF. Review the revision.")

    @app.get("/orders/{order_id}")
    def get_order(order_id: str):
        with closing(sqlite3.connect(database)) as db:
            return load_order(db, order_id)

    @app.get("/orders/{order_id}/history")
    def history(order_id: str):
        with closing(sqlite3.connect(database)) as db:
            load_order(db, order_id)
            rows = db.execute("""SELECT version, action, recorded_at, state FROM review_history
                                 WHERE order_id = ? ORDER BY version""", (order_id,))
            return [{"version": version, "action": action, "recorded_at_utc": timestamp,
                     **json.loads(state)} for version, action, timestamp, state in rows]

    @app.get("/orders/{order_id}/evidence")
    def evidence(order_id: str, version: int):
        with closing(sqlite3.connect(database)) as db:
            draft = load_order(db, order_id)
        check_version(draft, version)
        query = f"{draft['model']} mechanical seal kit"
        if draft["serial"]:
            query += " serial " + draft["serial"]
        if draft["revision"]:
            query += " revision " + draft["revision"]
        return [asdict(record) for record in shortlist(query, supplier_records())]

    @app.patch("/orders/{order_id}/clarification")
    def clarify(order_id: str, change: Clarification):
        updates = {key: value.strip().upper() for key, value in
                   change.model_dump(exclude={"version"}, exclude_none=True).items()}
        if not updates or not all(updates.values()):
            raise HTTPException(422, "Supply a serial number or revision.")
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            document = load_order(db, order_id)
            check_version(document, change.version)
            if document["status"] == "approved":
                raise HTTPException(409, "This order is already approved.")
            document.setdefault("original_evidence", {
                "serial": document["serial"], "revision": document["revision"]})
            document.update(updates)
            match = resolve_kit(document["model"], document["serial"], document["revision"])
            document["match"] = asdict(match)
            document["status"] = "awaiting_review" if match.part_code else "needs_clarification"
            return save_review(db, document, "clarified")

    @app.post("/orders/{order_id}/approval")
    def approve(order_id: str, review: ReviewVersion):
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            document = load_order(db, order_id)
            if document["status"] == "approved" and review.version in (
                    document["version"], document["version"] - 1):
                return document
            check_version(document, review.version)
            match = resolve_kit(document["model"], document["serial"], document["revision"])
            if not match.part_code or asdict(match) != document["match"]:
                raise HTTPException(409, "A current, supported match must be reviewed before approval.")
            document["status"] = "approved"
            return save_review(db, document, "approved")

    @app.get("/orders/{order_id}/export")
    def export(order_id: str):
        with closing(sqlite3.connect(database)) as db:
            order = load_order(db, order_id)
        output = StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["order_id", "line_number", "part_code", "quantity", "sales_unit"])
        if order["status"] == "approved":
            code = order["match"]["part_code"]
            match = resolve_kit(order["model"], order["serial"], order["revision"])
            rows = [row for row in read_rows("catalog.csv") if row["part_code"] == code]
            if match.part_code != code or len(rows) != 1:
                raise HTTPException(409, "The approved part requires a new catalog review.")
            part = rows[0]
            pack = int(part["kits_per_sales_unit"])
            if order["unit"] != "KIT" or pack < 1 or order["quantity"] % pack:
                raise HTTPException(409, "The ordered quantity requires a pack-size review.")
            writer.writerow([order["order_id"], order["line_number"], code,
                             order["quantity"] // pack, part["sales_unit"]])
        return Response(output.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{order_id}-approved.csv"'})

    @app.get("/samples/{sample_id}")
    def sample(sample_id: str):
        if sample_id not in {"PO-1001", "PO-1002"}:
            raise HTTPException(404, "Sample unavailable.")
        return FileResponse(Path(__file__).parent / "orders" / f"{sample_id}.pdf",
                            media_type="application/pdf")

    assets = Path(__file__).parent / "dist"
    if assets.is_dir():
        app.mount("/", StaticFiles(directory=assets, html=True), name="workspace")
    return app


app = create_app(Path(__file__).with_name("orderproof.sqlite3"))
