"""진행 기록 (SQLite). 중단 후 다시 실행해도 끝난 파일은 건너뛴다."""

import sqlite3
import threading
from pathlib import Path

PENDING, DONE, FAILED, SKIPPED = "pending", "done", "failed", "skipped"


class State:
    def __init__(self, path: Path):
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY, name TEXT, path TEXT, size INTEGER,
                    kind TEXT, mime TEXT, status TEXT DEFAULT 'pending',
                    google_id TEXT DEFAULT '', error TEXT DEFAULT '')"""
            )
            self._db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def add_items(self, items: list[dict]) -> None:
        """새로 발견한 파일만 추가한다. 이미 있는 파일의 상태는 건드리지 않는다."""
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR IGNORE INTO items (id, name, path, size, kind, mime) VALUES (?,?,?,?,?,?)",
                [(i["id"], i["name"], i["path"], i["size"], i["kind"], i["mime"]) for i in items],
            )

    def mark(self, item_id: str, status: str, google_id: str = "", error: str = "") -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE items SET status=?, google_id=?, error=? WHERE id=?",
                (status, google_id, error[:500], item_id),
            )

    def todo(self) -> list[dict]:
        """아직 옮기지 못한 파일 (대기 + 이전 실패)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, name, path, size, kind, mime FROM items "
                "WHERE status IN ('pending', 'failed') ORDER BY path"
            ).fetchall()
        keys = ("id", "name", "path", "size", "kind", "mime")
        return [dict(zip(keys, row)) for row in rows]

    def counts(self) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*), COALESCE(SUM(size),0) FROM items GROUP BY status").fetchall()
        result = {s: 0 for s in (PENDING, DONE, FAILED, SKIPPED)}
        result["total"] = 0
        result["done_bytes"] = 0
        for status, count, size in rows:
            result[status] = count
            result["total"] += count
            if status == DONE:
                result["done_bytes"] = size
        return result

    def problems(self) -> list[tuple[str, str, str]]:
        with self._lock:
            return self._db.execute(
                "SELECT path, status, error FROM items WHERE status IN ('failed','skipped') ORDER BY path"
            ).fetchall()

    def get_meta(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value))
