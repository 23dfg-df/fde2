import datetime
import json
import sqlite3
from pathlib import Path


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class Repository:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "doclab.sqlite3"
        with self.connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("CREATE TABLE IF NOT EXISTS pages (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, body TEXT NOT NULL)")

    def connect(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        return con

    def save_page(self, page):
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO pages VALUES (?,?)", (page["id"], json.dumps(page, ensure_ascii=False)))

    def pages(self):
        with self.connect() as con:
            return [json.loads(x["body"]) for x in con.execute("SELECT body FROM pages ORDER BY id")]

    def save_job(self, job):
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?)", (job["id"], job["created_at"], json.dumps(job, ensure_ascii=False)))

    def job(self, job_id):
        with self.connect() as con:
            row = con.execute("SELECT body FROM jobs WHERE id=?", (job_id,)).fetchone()
        return json.loads(row["body"]) if row else None

    def jobs(self, limit=50):
        with self.connect() as con:
            return [json.loads(x["body"]) for x in con.execute("SELECT body FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))]
