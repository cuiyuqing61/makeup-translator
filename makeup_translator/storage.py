"""Local SQLite documents. Each mutation commits atomically; legacy JSON is retained."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

class Storage:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # SQLite's backup API also captures committed WAL pages. One backup per migration.
        if path.exists():
            with sqlite3.connect(path) as existing:
                version = existing.execute('PRAGMA user_version').fetchone()[0]
                if version < 2:
                    backup_path = path.with_suffix(path.suffix + '.pre-agent-v2.bak')
                    if not backup_path.exists():
                        with sqlite3.connect(backup_path) as backup:
                            existing.backup(backup)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS documents (kind TEXT, id TEXT, payload TEXT NOT NULL, PRIMARY KEY(kind,id))')
            db.execute('PRAGMA user_version=2')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute('PRAGMA journal_mode=WAL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, kind, id):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM documents WHERE kind=? AND id=?', (kind,id)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, kind):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM documents WHERE kind=? ORDER BY rowid DESC', (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def put(self, kind, id, payload):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO documents VALUES (?,?,?)', (kind,id,json.dumps(payload,ensure_ascii=False)))

    def delete(self, kind, id):
        with self.connect() as db:
            db.execute('DELETE FROM documents WHERE kind=? AND id=?', (kind,id))
