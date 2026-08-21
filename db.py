from datetime import datetime, timedelta
import aiosqlite

from config import DB_PATH, PLANS

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    user_id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    kicked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    plan_id TEXT NOT NULL,
    rating INTEGER NOT NULL,
    text TEXT,
    created_at TEXT NOT NULL
);
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executescript(_INIT_SQL)
        await conn.commit()


async def ensure_user(user_id: int, username: str | None):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, created_at) VALUES (?, ?, ?)",
            (user_id, username, datetime.utcnow().isoformat()),
        )
        await conn.commit()


async def get_active_subscription(user_id: int):
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM subscriptions WHERE user_id = ? AND expires_at > ?",
            (user_id, datetime.utcnow().isoformat()),
        )
        return await cur.fetchone()


async def extend_subscription(user_id: int, days: int, channel_id: int | None = None) -> datetime:
    """Продлевает (или создаёт) подписку от текущего срока действия либо от сейчас, если истекла."""
    if channel_id is None:
        # берём channel_id из первого плана по умолчанию, если не передан явно
        channel_id = next(iter(PLANS.values()))["channel_id"]

    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT expires_at FROM subscriptions WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()

        now = datetime.utcnow()
        if row:
            current_expiry = datetime.fromisoformat(row["expires_at"])
            base = current_expiry if current_expiry > now else now
        else:
            base = now

        new_expiry = base + timedelta(days=days)

        await conn.execute(
            """
            INSERT INTO subscriptions (user_id, channel_id, expires_at, kicked)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                expires_at = excluded.expires_at,
                channel_id = excluded.channel_id,
                kicked = 0
            """,
            (user_id, channel_id, new_expiry.isoformat()),
        )
        await conn.commit()
        return new_expiry


async def pop_expired_subscriptions():
    """Возвращает истёкшие, ещё не обработанные подписки и помечает их как обработанные."""
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM subscriptions WHERE expires_at <= ? AND kicked = 0",
            (datetime.utcnow().isoformat(),),
        )
        rows = await cur.fetchall()

        if rows:
            ids = [r["user_id"] for r in rows]
            await conn.executemany(
                "UPDATE subscriptions SET kicked = 1 WHERE user_id = ?",
                [(i,) for i in ids],
            )
            await conn.commit()

        return rows


async def save_review(user_id: int, plan_id: str, rating: int, text: str | None):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO reviews (user_id, plan_id, rating, text, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, plan_id, rating, text, datetime.utcnow().isoformat()),
        )
        await conn.commit()
