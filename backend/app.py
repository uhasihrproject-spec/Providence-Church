import asyncio
import json
import os
import re
import secrets
import socket
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import qrcode
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "psws.db"
VERSE_DELAY_SECONDS = 0.45

BOOKS = [
    "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy", "Joshua", "Judges", "Ruth",
    "1 Samuel", "2 Samuel", "1 Kings", "2 Kings", "1 Chronicles", "2 Chronicles", "Ezra", "Nehemiah",
    "Esther", "Job", "Psalm", "Psalms", "Proverbs", "Ecclesiastes", "Song of Solomon", "Isaiah",
    "Jeremiah", "Lamentations", "Ezekiel", "Daniel", "Hosea", "Joel", "Amos", "Obadiah", "Jonah",
    "Micah", "Nahum", "Habakkuk", "Zephaniah", "Haggai", "Zechariah", "Malachi", "Matthew", "Mark",
    "Luke", "John", "Acts", "Romans", "1 Corinthians", "2 Corinthians", "Galatians", "Ephesians",
    "Philippians", "Colossians", "1 Thessalonians", "2 Thessalonians", "1 Timothy", "2 Timothy", "Titus",
    "Philemon", "Hebrews", "James", "1 Peter", "2 Peter", "1 John", "2 John", "3 John", "Jude",
    "Revelation",
]

BOOK_PATTERN = "|".join(sorted((re.escape(book) for book in BOOKS), key=len, reverse=True))
SCRIPTURE_REGEX = re.compile(
    rf"\\b(?P<book>{BOOK_PATTERN})\\s+(?P<chapter>\\d{{1,3}})(?:[:\\s](?P<verse_start>\\d{{1,3}})(?:-(?P<verse_end>\\d{{1,3}}))?)?\\b",
    re.IGNORECASE,
)

app = FastAPI(title="Providence Baptist Church Smart Worship System")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TranscriptRequest(BaseModel):
    text: str
    speaker: str = "unknown"


class SummaryRequest(BaseModel):
    text: str = Field(..., min_length=1)


class QRCreateRequest(BaseModel):
    label: str
    minutes_valid: int = Field(default=60, ge=1, le=60 * 24)
    single_use: bool = False


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.connections.discard(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for conn in list(self.connections):
            try:
                await conn.send_json(payload)
            except Exception:
                dead.append(conn)
        for conn in dead:
            self.disconnect(conn)


manager = ConnectionManager()
verse_cache: dict[str, dict[str, Any]] = {}


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS verse_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reference TEXT NOT NULL,
            text TEXT NOT NULL,
            speaker TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            input_text TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS qr_tokens (
            token TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            single_use INTEGER NOT NULL,
            used_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS qr_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL,
            valid INTEGER NOT NULL,
            scanned_at TEXT NOT NULL,
            ip_address TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_book(book: str) -> str:
    low = book.lower()
    for candidate in BOOKS:
        if candidate.lower() == low:
            return candidate
    return book.title()


def parse_scripture_references(text: str) -> list[str]:
    refs: list[str] = []
    for match in SCRIPTURE_REGEX.finditer(text):
        book = canonical_book(match.group("book"))
        chapter = match.group("chapter")
        verse_start = match.group("verse_start")
        verse_end = match.group("verse_end")
        if verse_start and verse_end:
            refs.append(f"{book} {chapter}:{verse_start}-{verse_end}")
        elif verse_start:
            refs.append(f"{book} {chapter}:{verse_start}")
        else:
            refs.append(f"{book} {chapter}")
    # de-duplicate preserving order
    return list(dict.fromkeys(refs))


def fetch_scripture(reference: str) -> dict[str, Any]:
    if reference in verse_cache:
        return verse_cache[reference]

    encoded = quote(reference)
    url = f"https://bible-api.com/{encoded}"
    with httpx.Client(timeout=12.0) as client:
        response = client.get(url)
        response.raise_for_status()
        data = response.json()
    verse_cache[reference] = data
    return data


async def emit_reference(reference: str, speaker: str, source: str = "bible-api") -> None:
    try:
        data = await asyncio.to_thread(fetch_scripture, reference)
    except Exception as exc:
        await manager.broadcast({"type": "error", "message": str(exc), "reference": reference})
        return

    verses = data.get("verses") or []
    if not verses and data.get("text"):
        verses = [{"book_name": reference.split()[0], "chapter": "", "verse": "", "text": data.get("text", "")}]

    for verse in verses:
        verse_ref = f"{verse.get('book_name', '')} {verse.get('chapter', '')}:{verse.get('verse', '')}".strip()
        verse_text = str(verse.get("text", "")).strip()
        payload = {
            "type": "verse",
            "reference": verse_ref,
            "text": verse_text,
            "speaker": speaker,
            "source": source,
            "requested_reference": reference,
        }
        await manager.broadcast(payload)
        conn = get_db()
        conn.execute(
            "INSERT INTO verse_logs (reference, text, speaker, source, created_at) VALUES (?, ?, ?, ?, ?)",
            (verse_ref, verse_text, speaker, source, now_iso()),
        )
        conn.commit()
        conn.close()
        await asyncio.sleep(VERSE_DELAY_SECONDS)


def local_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:
        return "localhost"
    finally:
        sock.close()


def validate_qr_token(token: str, ip_address: str | None = None) -> tuple[bool, str]:
    conn = get_db()
    row = conn.execute("SELECT * FROM qr_tokens WHERE token = ?", (token,)).fetchone()
    valid = False
    reason = "Invalid token"
    if row:
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at < datetime.now(timezone.utc):
            reason = "Token expired"
        elif row["single_use"] and row["used_count"] > 0:
            reason = "Token already used"
        else:
            valid = True
            reason = "OK"
            conn.execute("UPDATE qr_tokens SET used_count = used_count + 1 WHERE token = ?", (token,))
    conn.execute(
        "INSERT INTO qr_scans (token, valid, scanned_at, ip_address) VALUES (?, ?, ?, ?)",
        (token, 1 if valid else 0, now_iso(), ip_address),
    )
    conn.commit()
    conn.close()
    return valid, reason


def heuristic_summary(text: str) -> dict[str, Any]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    short = " ".join(lines)[:500]
    scriptures = parse_scripture_references(text)
    key_points = lines[:3] if lines else [short[:120]]
    action_items = [f"Pray over: {scriptures[0]}" if scriptures else "Reflect and pray as a church body"]
    return {
        "summary": short or "No transcript text provided.",
        "key_points": key_points,
        "action_items": action_items,
        "detected_scriptures": scriptures,
        "short_prayer": "Lord, help us live Your Word with faith, unity, and love. Amen.",
        "source": "heuristic",
    }


def openai_summary(text: str) -> dict[str, Any] | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    prompt = (
        "Return strict JSON with keys summary, key_points(array), action_items(array), "
        "detected_scriptures(array), short_prayer from this transcript:\n" + text
    )

    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
            )
            response.raise_for_status()
            body = response.json()
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        parsed["source"] = "openai"
        return parsed
    except Exception:
        return None


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/")
def root() -> dict[str, str]:
    return {"status": "Providence Smart Worship Backend Running"}


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "websockets": len(manager.connections), "db": str(DB_PATH)}


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    await websocket.send_json({"type": "status", "message": "connected"})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.post("/transcript")
async def transcript(payload: TranscriptRequest) -> dict[str, Any]:
    references = parse_scripture_references(payload.text)
    for reference in references:
        asyncio.create_task(emit_reference(reference, payload.speaker, "transcript"))
    return {"ok": True, "detected_references": references}


@app.get("/scripture")
async def scripture(reference: str, speaker: str = "manual") -> dict[str, Any]:
    await emit_reference(reference, speaker, "manual")
    return {"ok": True, "reference": reference}


@app.post("/notes/summary")
def notes_summary(payload: SummaryRequest) -> dict[str, Any]:
    summary = openai_summary(payload.text) or heuristic_summary(payload.text)
    conn = get_db()
    conn.execute(
        "INSERT INTO summaries (input_text, summary_json, source, created_at) VALUES (?, ?, ?, ?)",
        (payload.text, json.dumps(summary), summary.get("source", "heuristic"), now_iso()),
    )
    conn.commit()
    conn.close()
    return summary


@app.post("/admin/create_qr")
def create_qr(payload: QRCreateRequest) -> dict[str, Any]:
    token = secrets.token_urlsafe(24)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=payload.minutes_valid)
    conn = get_db()
    conn.execute(
        "INSERT INTO qr_tokens (token, label, expires_at, single_use, used_count, created_at) VALUES (?, ?, ?, ?, 0, ?)",
        (token, payload.label, expires_at.isoformat(), 1 if payload.single_use else 0, now_iso()),
    )
    conn.commit()
    conn.close()
    return {"token": token, "expires_at": expires_at.isoformat(), "single_use": payload.single_use}


@app.get("/qr/{token}.png")
def qr_png(token: str) -> Response:
    frontend_port = int(os.getenv("FRONTEND_PORT", "3000"))
    ip = local_lan_ip()
    target = f"http://{ip}:{frontend_port}/?token={token}"
    image = qrcode.make(target)
    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")


@app.get("/validate_qr")
def validate_qr(token: str = Query(...), ip: str | None = Query(default=None)) -> dict[str, Any]:
    valid, reason = validate_qr_token(token, ip)
    return {"valid": valid, "reason": reason}
