"""
cache.py — SQLite analysis cache for resumable batch processing.

Stores analysis results keyed by file path + mtime, so re-analysis is
only triggered when the file actually changes.
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional


DEFAULT_DB_PATH = Path.home() / ".trackstage" / "analysis.db"

_CREATE = """
CREATE TABLE IF NOT EXISTS analysis (
    path       TEXT NOT NULL,
    mtime      REAL NOT NULL,
    size       INTEGER NOT NULL,
    result     TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (path, mtime, size)
);
"""


class AnalysisCache:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute(_CREATE)
        self.conn.commit()

    def get(self, file_path: Path) -> Optional[dict]:
        stat = file_path.stat()
        row = self.conn.execute(
            "SELECT result FROM analysis WHERE path=? AND mtime=? AND size=?",
            (str(file_path), stat.st_mtime, stat.st_size),
        ).fetchone()
        if row:
            return json.loads(row[0])
        return None

    def put(self, file_path: Path, result: dict):
        stat = file_path.stat()
        self.conn.execute(
            "INSERT OR REPLACE INTO analysis (path, mtime, size, result) VALUES (?, ?, ?, ?)",
            (str(file_path), stat.st_mtime, stat.st_size, json.dumps(result)),
        )
        self.conn.commit()

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM analysis").fetchone()
        return row[0]

    def clear(self):
        self.conn.execute("DELETE FROM analysis")
        self.conn.commit()

    def close(self):
        self.conn.close()
