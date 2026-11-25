from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "tracker.db"

DEFAULT_ALERT = {
    "id": 1,
    "enabled": 0,
    "email": "",
    "origin": "YOW",
    "destination": "YVR",
    "window_start": "2026-12-18",
    "window_end": "2027-01-04",
    "min_nights": 6,
    "max_nights": 9,
    "nonstop_only": 0,
    "include_carry_on": 1,
    "max_price_cad": 850,
    "daily_summary": 0,
    "last_checked_at": None,
    "last_emailed_at": None,
    "last_emailed_price": None,
}


async def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS email_alert (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 0,
                email TEXT NOT NULL DEFAULT '',
                origin TEXT NOT NULL DEFAULT 'YOW',
                destination TEXT NOT NULL DEFAULT 'YVR',
                window_start TEXT NOT NULL DEFAULT '2026-12-18',
                window_end TEXT NOT NULL DEFAULT '2027-01-04',
                min_nights INTEGER NOT NULL DEFAULT 6,
                max_nights INTEGER NOT NULL DEFAULT 9,
                nonstop_only INTEGER NOT NULL DEFAULT 0,
                include_carry_on INTEGER NOT NULL DEFAULT 1,
                max_price_cad INTEGER NOT NULL DEFAULT 850,
                daily_summary INTEGER NOT NULL DEFAULT 0,
                last_checked_at TEXT,
                last_emailed_at TEXT,
                last_emailed_price INTEGER
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS search_cache (
                cache_key TEXT PRIMARY KEY,
                fetched_at REAL NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        cur = await db.execute("SELECT COUNT(*) FROM email_alert WHERE id = 1")
        row = await cur.fetchone()
        if not row or row[0] == 0:
            cols = ", ".join(DEFAULT_ALERT.keys())
            placeholders = ", ".join("?" for _ in DEFAULT_ALERT)
            await db.execute(
                f"INSERT INTO email_alert ({cols}) VALUES ({placeholders})",
                tuple(DEFAULT_ALERT.values()),
            )
        await db.commit()


async def get_email_alert() -> dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM email_alert WHERE id = 1")
        row = await cur.fetchone()
        if not row:
            return dict(DEFAULT_ALERT)
        d = dict(row)
        for k in ("enabled", "nonstop_only", "include_carry_on", "daily_summary"):
            d[k] = bool(d[k])
        return d


async def save_email_alert(data: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "enabled",
        "email",
        "origin",
        "destination",
        "window_start",
        "window_end",
        "min_nights",
        "max_nights",
        "nonstop_only",
        "include_carry_on",
        "max_price_cad",
        "daily_summary",
        "last_checked_at",
        "last_emailed_at",
        "last_emailed_price",
    }
    updates = {}
    for k, v in data.items():
        if k not in allowed:
            continue
        updates[k] = (1 if v else 0) if isinstance(v, bool) else v

    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                f"UPDATE email_alert SET {set_clause} WHERE id = 1",
                tuple(updates.values()),
            )
            await db.commit()
    return await get_email_alert()


async def get_cached_pair(cache_key: str, ttl_seconds: int = 600) -> dict[str, Any] | None:
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT fetched_at, payload_json FROM search_cache WHERE cache_key = ?",
            (cache_key,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        fetched_at, payload_json = row
        if now - fetched_at > ttl_seconds:
            return None
        try:
            return json.loads(payload_json)
        except Exception:
            return None


async def set_cached_pair(cache_key: str, payload: dict[str, Any]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO search_cache (cache_key, fetched_at, payload_json)
            VALUES (?, ?, ?)
            """,
            (cache_key, time.time(), json.dumps(payload)),
        )
        await db.commit()
