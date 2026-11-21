"""Local single-buyer order intake. Run with uvicorn api:app."""
import csv
import hashlib
import json
import os
import re
from contextlib import asynccontextmanager
from dataclasses import asdict
from io import BytesIO, StringIO
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, UploadFile

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, ConfigDict, Field

from demo_privacy import isolate_visitor, storage_key
from intake import IntakeError, read_order_stream
from matching import resolve_kit
from catalog import rows as catalog_rows, DEFAULT
from review_flow import ReviewConflict, advance
from storage import configured_database, transaction
from retrieval import shortlist, supplier_records

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class ReviewVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class Clarification(ReviewVersion):
    serial: str | None = Field(default=None, max_length=64)
    revision: str | None = Field(default=None, max_length=32)


def load_order(db, order_id, *, lock=False):
    sql = "SELECT document FROM drafts WHERE order_id = ?"
    if lock and db.postgres:
        sql += " FOR UPDATE"
    row = db.execute(sql, (storage_key(order_id.upper()),)).fetchone()
    if row is None:
        raise HTTPException(404, "Order not found.")
    document = json.loads(row[0])
    document.setdefault("version", 1)  # Drafts saved before review versioning.
    return document


def record_event(db, document, action):
    state = {key: document[key] for key in ("status", "serial", "revision", "match")}
    db.execute("""INSERT INTO review_history (order_id, version, action, state)
                  VALUES (?, ?, ?, ?)""",
               (storage_key(document["order_id"]), document["version"], action, json.dumps(state)))


def save_review(db, document, action):
    db.execute("UPDATE drafts SET document = ? WHERE order_id = ?",
               (json.dumps(document), storage_key(document["order_id"])))
    record_event(db, document, action)
    return document


def check_version(document, supplied):
    if document["version"] != supplied:
        raise HTTPException(409, "The draft changed. Reload before reviewing it.")


def create_app(database: Path | str, *, demo: bool | None = None) -> FastAPI:
    if demo is None:
        demo = bool(os.getenv("VERCEL")) or os.getenv("ORDERPROOF_DEMO") == "1"
    if demo and Path(os.getenv("ORDERPROOF_CATALOG_PATH") or DEFAULT).resolve() != DEFAULT:
        raise RuntimeError("The anonymous demo requires the bundled fictional catalog")
    sample_hashes = {hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in (Path(__file__).parent / "orders").glob("PO-*.pdf")} if demo else set()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with transaction(database, write=True) as db:
            if db.postgres:
                db.execute("SELECT pg_advisory_xact_lock(763452901)")
            id_type = "TEXT" if db.postgres else "TEXT COLLATE NOCASE"
            binary_type = "BYTEA" if db.postgres else "BLOB"
            timestamp = "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')" if db.postgres else "CURRENT_TIMESTAMP"
            db.execute(f"""CREATE TABLE IF NOT EXISTS drafts (
                order_id {id_type} PRIMARY KEY,
                fingerprint TEXT NOT NULL UNIQUE,
                document TEXT NOT NULL
            )""")
            db.execute(f"""CREATE TABLE IF NOT EXISTS review_checkpoints (
                order_id {id_type} PRIMARY KEY, encoding TEXT NOT NULL,
                payload {binary_type} NOT NULL)""")
            db.execute(f"""CREATE TABLE IF NOT EXISTS review_history (
                order_id {id_type} NOT NULL,
                version INTEGER NOT NULL,
                action TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT {timestamp},
                state TEXT NOT NULL,
                PRIMARY KEY (order_id, version)
            )""")
            for order_id, in db.execute("""SELECT order_id FROM drafts d WHERE NOT EXISTS
                    (SELECT 1 FROM review_history h WHERE h.order_id = d.order_id)"""):
                record_event(db, load_order(db, order_id), "existing_draft")
        yield

    app = FastAPI(title="OrderProof", lifespan=lifespan)

    if demo:
        app.middleware("http")(isolate_visitor)

    @app.post("/orders", status_code=201)
    def upload(file: UploadFile, response: Response):
        content = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "The PDF must be 5 MiB or smaller.")
        if not content.startswith(b"%PDF-"):
            raise HTTPException(422, "Select a text-based PDF purchase order.")
        fingerprint = hashlib.sha256(content).hexdigest()
        if demo and fingerprint not in sample_hashes:
            raise HTTPException(422, "This public demo accepts only the bundled fictional PDFs.")
        fingerprint = storage_key(fingerprint)
        with transaction(database, write=True) as db:
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
                (storage_key(draft.order_id), fingerprint, json.dumps(result)),
            )
            if inserted.rowcount:
                advance(db, result)
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
        with transaction(database) as db:
            return load_order(db, order_id)

    @app.get("/orders/{order_id}/history")
    def history(order_id: str):
        with transaction(database) as db:
            load_order(db, order_id)
            rows = db.execute("""SELECT version, action, recorded_at, state FROM review_history
                                 WHERE order_id = ? ORDER BY version""", (storage_key(order_id.upper()),))
            return [{"version": version, "action": action, "recorded_at_utc": timestamp,
                     **json.loads(state)} for version, action, timestamp, state in rows]

    @app.get("/orders/{order_id}/evidence")
    def evidence(order_id: str, version: int):
        with transaction(database) as db:
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
        with transaction(database, write=True) as db:
            document = load_order(db, order_id, lock=True)
            check_version(document, change.version)
            if document["status"] == "approved":
                raise HTTPException(409, "This order is already approved.")
            if demo:
                rules = [r for r in catalog_rows("serial_ranges") if r["pump_model"] == document["model"]]
                revisions = {r["pump_revision"] for r in rules}
                patterns = {re.escape(r["prefix"]) + r"[0-9]{%d}" % len(r["first_suffix"])
                            for r in rules}
                if ("revision" in updates and updates["revision"] not in revisions
                        or "serial" in updates and not any(re.fullmatch(p, updates["serial"]) for p in patterns)):
                    raise HTTPException(422, "Use a fictional serial or revision for this pump model.")
            document = advance(db, document, {"action": "clarify", "updates": updates})
            return save_review(db, document, "clarified")

    @app.post("/orders/{order_id}/approval")
    def approve(order_id: str, review: ReviewVersion):
        with transaction(database, write=True) as db:
            document = load_order(db, order_id, lock=True)
            if document["status"] == "approved" and review.version in (
                    document["version"], document["version"] - 1):
                return document
            check_version(document, review.version)
            try:
                document = advance(db, document, {"action": "approve"})
            except ReviewConflict as exc:
                raise HTTPException(409, str(exc)) from exc
            return save_review(db, document, "approved")

    @app.get("/orders/{order_id}/export")
    def export(order_id: str):
        with transaction(database) as db:
            order = load_order(db, order_id)
        output = StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["order_id", "line_number", "part_code", "quantity", "sales_unit"])
        if order["status"] == "approved":
            code = order["match"]["part_code"]
            match = resolve_kit(order["model"], order["serial"], order["revision"])
            rows = [row for row in catalog_rows("products") if row["part_code"] == code]
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

    @app.get("/catalog")
    def catalog():
        return {table: catalog_rows(table) for table in ("products", "compatibility", "serial_ranges")}

    @app.get("/samples/{sample_id}")
    def sample(sample_id: str):
        if sample_id not in {"PO-1001", "PO-1002", "PO-1003"}:
            raise HTTPException(404, "Sample unavailable.")
        return FileResponse(Path(__file__).parent / "orders" / f"{sample_id}.pdf",
                            media_type="application/pdf",
                            headers={"X-OrderProof-Mode": "fictional-demo" if demo else "local"})

    assets = Path(__file__).parent / "dist"
    if assets.is_dir():
        app.mount("/", StaticFiles(directory=assets, html=True), name="workspace")
    return app


app = create_app(configured_database())
