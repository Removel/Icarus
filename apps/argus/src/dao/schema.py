"""Reject old trial schemas without modifying or deleting their data."""
import sqlite3

VERSION = 3


class SchemaUpgradeRequired(RuntimeError):
    pass


def check_schema(path):
    if not path.exists():
        return
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
        has_table = db.execute("SELECT 1 FROM sqlite_master WHERE name='observations'").fetchone()
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if has_table and version != VERSION:
            raise SchemaUpgradeRequired('Stop Argus; the old trial database requires explicit rebuilding. No automatic migration or deletion.')
