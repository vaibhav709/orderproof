"""Local single-buyer order intake. Run with uvicorn api:app."""
import hashlib
import json
import sqlite3
from contextlib import asynccontextmanager, closing
from dataclasses import asdict
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, UploadFile

from intake import IntakeError, read_order_stream

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


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
                return json.loads(existing[0])
            try:
                draft = read_order_stream(BytesIO(content))
            except IntakeError as exc:
                raise HTTPException(422, str(exc)) from exc
            result = asdict(draft)
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
                return json.loads(existing[0])
            raise HTTPException(409, "This order number already has a different PDF. Review the revision.")

    @app.get("/orders/{order_id}")
    def get_order(order_id: str):
        with closing(sqlite3.connect(database)) as db:
            row = db.execute("SELECT document FROM drafts WHERE order_id = ?", (order_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Order not found.")
        return json.loads(row[0])

    return app


app = create_app(Path(__file__).with_name("orderproof.sqlite3"))
