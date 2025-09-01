# details_cache.py
import sqlite3

DB = "seen.db"


def _init():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS details(
        url TEXT PRIMARY KEY,
        title TEXT,
        location TEXT,
        provider TEXT,
        updated_at TEXT DEFAULT (datetime('now'))
    )"""
    )
    conn.commit()
    conn.close()


def get(url: str):
    _init()
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT title, location, provider FROM details WHERE url=?", (url,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "title": row[0] or "",
            "location": row[1] or "",
            "provider": row[2] or "",
        }
    return None


def put(url: str, title: str, location: str, provider: str = ""):
    _init()
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute(
        """INSERT OR REPLACE INTO details(url, title, location, provider, updated_at)
                 VALUES(?,?,?,?,datetime('now'))""",
        (url, title or "", location or "", provider or ""),
    )
    conn.commit()
    conn.close()
