import asyncio
import json
import sqlite3
import time
from contextlib import closing

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, muted_until INTEGER NOT NULL DEFAULT 0,
  categories TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS hosts (
  id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, host TEXT NOT NULL, category TEXT NOT NULL, code TEXT NOT NULL,
  message TEXT NOT NULL, created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_created ON events(created);
CREATE TABLE IF NOT EXISTS cooldowns (
  host TEXT NOT NULL, code TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(host, code)
);
CREATE TABLE IF NOT EXISTS deliveries (
  event INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  user INTEGER NOT NULL, PRIMARY KEY(event, user)
);
CREATE TABLE IF NOT EXISTS operations (
  id INTEGER PRIMARY KEY, user INTEGER NOT NULL, host TEXT NOT NULL,
  app TEXT NOT NULL, action TEXT NOT NULL, outcome TEXT NOT NULL, updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS operations_updated ON operations(updated);
"""


class Storage:
    def __init__(self, path):
        self.path = path
        self.lock = asyncio.Lock()

    async def call(self, callback):
        # Serialize submissions. Connections are short lived and confined to worker threads.
        async with self.lock:
            worker = asyncio.create_task(asyncio.to_thread(self._call, callback))
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                await worker
                raise

    def _call(self, callback):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("PRAGMA foreign_keys = ON")
            return callback(connection)

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)

        def initialize(db):
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Unsupported database schema")
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("BEGIN; " + SCHEMA + " PRAGMA user_version = 1; COMMIT;")
            db.execute(
                "UPDATE operations SET outcome='interrupted', updated=? "
                "WHERE outcome IN ('submitting','accepted','queued','in_progress')",
                (time.time(),),
            )

        await self.call(initialize)

    @staticmethod
    def _preferences(db, user):
        return db.execute(
            "SELECT muted_until, categories FROM users WHERE id=?", (user,)
        ).fetchone()

    async def preferences(self, user):
        row = await self.call(lambda db: self._preferences(db, user))
        return (row[0], set(json.loads(row[1]))) if row else (0, set())

    async def set_preferences(self, user, *, mute=None, category=None):
        def update(db):
            until, raw = self._preferences(db, user) or (0, "[]")
            disabled = set(json.loads(raw))
            if category is not None:
                disabled.symmetric_difference_update({category})
            db.execute(
                "INSERT OR REPLACE INTO users VALUES (?,?,?)",
                (user, until if mute is None else mute, json.dumps(sorted(disabled))),
            )

        await self.call(update)

    async def snapshots(self):
        rows = await self.call(lambda db: db.execute("SELECT id,snapshot FROM hosts").fetchall())
        return {host: json.loads(snapshot) for host, snapshot in rows}

    async def record(self, host, snapshot, events, users, cooldown, now):
        def insert(db):
            db.execute(
                "INSERT OR REPLACE INTO hosts VALUES (?,?,?)", (host, json.dumps(snapshot), now)
            )
            for category, code, message in events:
                event_id = db.execute(
                    "INSERT INTO events(host,category,code,message,created) VALUES (?,?,?,?,?)",
                    (host, category, code, message, now),
                ).lastrowid
                previous = db.execute(
                    "SELECT created FROM cooldowns WHERE host=? AND code=?", (host, code)
                ).fetchone()
                if previous and (now - previous[0] < cooldown or now == previous[0]):
                    continue
                receivers = []
                for user in users:
                    until, disabled = self._preferences(db, user) or (0, "[]")
                    if until <= now and category not in json.loads(disabled):
                        receivers.append((event_id, user))
                if receivers:
                    db.executemany("INSERT INTO deliveries VALUES (?,?)", receivers)
                    db.execute("INSERT OR REPLACE INTO cooldowns VALUES (?,?,?)", (host, code, now))

        await self.call(insert)

    async def pending(self, now):
        return await self.call(
            lambda db: db.execute(
                "SELECT d.event,d.user,e.host,e.category,e.message FROM deliveries d "
                "JOIN events e ON e.id=d.event WHERE e.created>=? ORDER BY d.event,d.user LIMIT 50",
                (now - 3600,),
            ).fetchall()
        )

    async def delivered(self, event, user):
        await self.call(
            lambda db: db.execute(
                "DELETE FROM deliveries WHERE event=? AND user=?",
                (event, user),
            )
        )

    async def operation(self, user, host, app, action):
        return await self.call(
            lambda db: (
                db.execute(
                    "INSERT INTO operations(user,host,app,action,outcome,updated) VALUES (?,?,?,?,?,?)",
                    (user, host, app, action, "submitting", time.time()),
                ).lastrowid
            )
        )

    async def outcome(self, operation, outcome):
        await self.call(
            lambda db: db.execute(
                "UPDATE operations SET outcome=?, updated=? WHERE id=?",
                (outcome, time.time(), operation),
            )
        )

    async def prune(self, now, days):
        def delete(db):
            for table, column in (
                ("operations", "updated"),
                ("hosts", "updated"),
                ("events", "created"),
                ("cooldowns", "created"),
            ):
                db.execute(f"DELETE FROM {table} WHERE {column} < ?", (now - days * 86400,))
            db.execute(
                "DELETE FROM deliveries WHERE event IN (SELECT id FROM events WHERE created < ?)",
                (now - 3600,),
            )

        await self.call(delete)
