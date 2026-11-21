"""Local single-buyer order intake. Run with uvicorn api:app."""
import hashlib
import json
import sqlite3
from contextlib import asynccontextmanager, closing
from dataclasses import asdict
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, UploadFile

from pydantic import BaseModel, ConfigDict, Field

from intake import IntakeError, read_order_stream
from matching import resolve_kit

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


def save_review(db, document):
    document["version"] += 1
    db.execute("UPDATE drafts SET document = ? WHERE order_id = ?",
               (json.dumps(document), document["order_id"]))
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
            return save_review(db, document)

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
            return save_review(db, document)

    return app


app = create_app(Path(__file__).with_name("orderproof.sqlite3"))
