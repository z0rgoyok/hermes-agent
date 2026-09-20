"""Durable image intake, immutable extraction revisions and album admission ledger."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import tempfile
from contextlib import contextmanager


class InvoiceStore:
    def __init__(self, home: Path):
        self.root = Path(home) / "invoice-intake"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.originals = self.root / "originals"
        self.originals.mkdir(mode=0o700, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, extension TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    available REAL NOT NULL DEFAULT 0, error TEXT,
                    candidates TEXT, revision INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS revisions (
                    document_id TEXT, revision INTEGER, card TEXT NOT NULL,
                    warnings TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(document_id, revision));
                CREATE TABLE IF NOT EXISTS albums (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, changed REAL NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1, admitted INTEGER NOT NULL DEFAULT 0,
                    sealed INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS messages (
                    chat_id TEXT, message_id TEXT, document_id TEXT NOT NULL,
                    album_id TEXT NOT NULL, metadata TEXT NOT NULL,
                    delivery_status TEXT NOT NULL DEFAULT 'not_recorded',
                    PRIMARY KEY(chat_id, message_id));
                CREATE TABLE IF NOT EXISTS controls (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS recognition_attempts (
                    document_id TEXT NOT NULL, provider TEXT NOT NULL, outcome TEXT NOT NULL,
                    detail TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS questions (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, chat_id TEXT NOT NULL,
                    message_id TEXT NOT NULL, text TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0);
            """)
        os.chmod(self.root / "queue.sqlite", 0o600)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.root / "queue.sqlite", timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def ingest(self, data: bytes, extension: str, source: dict, metadata: dict,
               batch: str | None = None) -> str:
        digest = hashlib.sha256(data).hexdigest()
        extension = extension.lower()
        if extension not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}:
            raise ValueError("unsupported image extension")
        chat, message = str(source["chat_id"]), str(metadata["message_id"])
        album = batch or f"{chat}:{metadata.get('media_group_id') or message}"
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT document_id FROM messages WHERE chat_id=? AND message_id=?",
                                  (chat, message)).fetchone()
            if existing:
                return existing[0]
            path = self.originals / (digest + extension)
            if not path.exists():
                with tempfile.NamedTemporaryFile(dir=self.originals, delete=False) as out:
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
                    temporary = out.name
                os.replace(temporary, path)
            db.execute("INSERT OR IGNORE INTO documents(id,extension) VALUES (?,?)", (digest, extension))
            db.execute("""INSERT INTO albums(id,source,changed,sealed) VALUES (?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET changed=excluded.changed, version=version+1""",
                       (album, json.dumps(source), time.time(), int(batch is None)))
            db.execute("INSERT INTO messages(chat_id,message_id,document_id,album_id,metadata) VALUES (?,?,?,?,?)",
                       (chat, message, digest, album, json.dumps(metadata, ensure_ascii=False)))
        return digest

    def seal(self, batch: str):
        with self.db() as db:
            db.execute("UPDATE albums SET sealed=1,changed=? WHERE id=?", (time.time(), batch))

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE documents SET status='pending' WHERE status='processing'")

    def control(self, key: str, value: str | None = None):
        with self.db() as db:
            if value is not None:
                db.execute("INSERT OR REPLACE INTO controls VALUES (?,?)", (key, value))
            row = db.execute("SELECT value FROM controls WHERE key=?", (key,)).fetchone()
            return row[0] if row else ""

    def claim(self):
        jobs = self.claim_batch(1)
        return jobs[0] if jobs else None

    def claim_batch(self, limit=5):
        if not 1 <= limit <= 5:
            raise ValueError("batch size must be between 1 and 5")
        if self.control("paused"):
            return []
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT * FROM documents WHERE status='pending' AND available<=? ORDER BY rowid LIMIT ?",
                              (time.time(), limit)).fetchall()
            for row in rows:
                db.execute("UPDATE documents SET status='processing',attempts=attempts+1 WHERE id=?", (row["id"],))
            return [dict(row) for row in rows]

    def finish(self, document: str, card: dict, warnings: list[str]):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute("SELECT revision FROM documents WHERE id=?", (document,)).fetchone()[0] + 1
            partial = (card["document_type"] == "unreadable" or bool(card["uncertainties"]) or bool(warnings)
                       or (card["document_type"] == "invoice" and any(card[k] is None for k in ("client", "date", "total"))))
            db.execute("INSERT INTO revisions VALUES (?,?,?,?,?)",
                       (document, revision, json.dumps(card, ensure_ascii=False), json.dumps(warnings), time.time()))
            db.execute("UPDATE documents SET status=?,revision=?,error=NULL,candidates=NULL WHERE id=?",
                       ("partial" if partial else "ready", revision, document))

    def fail(self, document: str, reason: str, retry: bool = False):
        with self.db() as db:
            db.execute("UPDATE documents SET status=?,error=?,available=? WHERE id=?",
                       ("pending" if retry else "error", reason, time.time() + 30, document))

    def record_attempt(self, document: str, provider: str, outcome: str, detail: str):
        with self.db() as db:
            db.execute("INSERT INTO recognition_attempts VALUES (?,?,?,?,?)",
                       (document, provider, outcome, detail, time.time()))

    def ask(self, document: str, chat: str, message: str, text: str):
        if not text.strip() or len(text) > 3000:
            raise ValueError("question must contain 1–3000 characters")
        identifier = hashlib.sha256(json.dumps([document, chat, message, text]).encode()).hexdigest()
        with self.db() as db:
            if not db.execute("SELECT 1 FROM messages WHERE document_id=? AND chat_id=? AND message_id=?",
                              (document, chat, message)).fetchone():
                raise ValueError("question source must be an original message for this document")
            db.execute("INSERT OR IGNORE INTO questions(id,document_id,chat_id,message_id,text) VALUES (?,?,?,?,?)",
                       (identifier, document, chat, message, text))
        return identifier

    def pending_questions(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM questions WHERE sent=0 ORDER BY rowid")]

    def question_sent(self, identifier):
        with self.db() as db:
            db.execute("UPDATE questions SET sent=1 WHERE id=?", (identifier,))

    def retry_errors(self, album: str):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            ids = [row[0] for row in db.execute("""SELECT DISTINCT d.id FROM documents d
                JOIN messages m ON m.document_id=d.id WHERE m.album_id=? AND d.status='error'""", (album,))]
            for document in ids:
                db.execute("UPDATE documents SET status='pending',attempts=0,available=0 WHERE id=?", (document,))
                db.execute("""UPDATE albums SET version=version+1,changed=? WHERE id IN
                    (SELECT album_id FROM messages WHERE document_id=?)""", (time.time(), document))
            return len(ids)

    def review(self, document: str, candidates: list[str]):
        if not 1 <= len(candidates) <= 5 or any(len(x) > 500 for x in candidates):
            raise ValueError("provide 1–5 short candidates")
        with self.db() as db:
            cursor = db.execute("""UPDATE documents SET status='pending',attempts=0,available=0,candidates=?
                WHERE id=? AND status IN ('ready','partial','error')""", (json.dumps(candidates), document))
            if cursor.rowcount != 1:
                raise ValueError("document missing or already processing")
            db.execute("UPDATE albums SET version=version+1,changed=? WHERE id IN (SELECT album_id FROM messages WHERE document_id=?)",
                       (time.time(), document))

    def card(self, document: str):
        with self.db() as db:
            row = db.execute("SELECT id,status,revision,error FROM documents WHERE id=?", (document,)).fetchone()
            if row is None:
                raise ValueError("unknown document")
            result = dict(row)
            result["recognition_attempts"] = [dict(r) for r in db.execute(
                "SELECT provider,outcome,detail,created FROM recognition_attempts WHERE document_id=? ORDER BY created", (document,))]
            result["revisions"] = [dict(r) for r in db.execute(
                "SELECT revision,card,warnings,created FROM revisions WHERE document_id=? ORDER BY revision", (document,))]
            for revision in result["revisions"]:
                revision["card"] = json.loads(revision["card"])
                revision["warnings"] = json.loads(revision["warnings"])
            result["sources"] = [json.loads(r[0]) for r in db.execute("SELECT metadata FROM messages WHERE document_id=?", (document,))]
            return result

    def list_documents(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT id,status,revision,error FROM documents ORDER BY rowid")]

    def ready_albums(self):
        with self.db() as db:
            rows = db.execute("""SELECT * FROM albums a WHERE sealed=1 AND version>admitted AND changed<?
                AND NOT EXISTS (SELECT 1 FROM messages m JOIN documents d ON d.id=m.document_id
                    WHERE m.album_id=a.id AND d.status IN ('pending','processing'))""", (time.time() - 3,)).fetchall()
            return [dict(r) for r in rows]

    def album_progress(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("""SELECT a.id,a.source,a.version,
                count(*) AS total,
                sum(d.status='ready') AS ready, sum(d.status='partial') AS partial,
                sum(d.status='error') AS errors, sum(d.status='processing') AS processing
                FROM albums a JOIN messages m ON m.album_id=a.id
                JOIN documents d ON d.id=m.document_id
                WHERE a.sealed=1 GROUP BY a.id""")]

    def album_cards(self, album: str):
        with self.db() as db:
            ids = [r[0] for r in db.execute("SELECT DISTINCT document_id FROM messages WHERE album_id=?", (album,))]
            count = db.execute("SELECT count(*) FROM messages WHERE album_id=?", (album,)).fetchone()[0]
        return {"received_images": count, "unique_images": len(ids), "documents": [self.card(d) for d in ids]}

    def admitted(self, album: str, version: int):
        with self.db() as db:
            db.execute("UPDATE albums SET admitted=max(admitted,?) WHERE id=?", (version, album))
