# -*- coding: utf-8 -*-
"""SQLite 数据存储 — 任务历史与账户状态。"""

# ================================
# 导入依赖
# ================================
import json
import aiosqlite
from pathlib import Path
from datetime import datetime, timezone


# ================================
# 常量
# ================================
DB_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DB_DIR / "freeload.db"


# ================================
# 数据库管理器
# ================================
class Storage:
    """任务历史与账户状态的持久化存储。"""

    def __init__(self, db_path: str = str(DB_PATH)):
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    # ================================
    # 初始化
    # ================================
    async def initialize(self) -> None:
        """创建数据库与表结构。"""
        DB_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row

        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS task_history (
                id          TEXT PRIMARY KEY,
                platform    TEXT NOT NULL,
                event_type  TEXT NOT NULL,
                title       TEXT,
                value       REAL DEFAULT 0,
                success     INTEGER DEFAULT 0,
                detail      TEXT,
                data        TEXT,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS account_state (
                platform        TEXT PRIMARY KEY,
                cookie_valid    INTEGER DEFAULT 0,
                last_login      TEXT,
                last_active     TEXT,
                today_earnings  REAL DEFAULT 0,
                total_earnings  REAL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_history_platform
                ON task_history(platform, created_at);
        """)
        await self._conn.commit()
        print(f"💾 数据库已初始化: {DB_PATH}")

    async def close(self) -> None:
        """关闭数据库连接。"""
        if self._conn:
            await self._conn.close()

    # ================================
    # 任务历史
    # ================================
    async def save_task(
        self,
        task_id: str,
        platform: str,
        event_type: str,
        title: str,
        value: float,
        success: bool,
        detail: str = "",
        data: dict | None = None,
    ) -> None:
        """保存一条任务执行记录。"""
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """INSERT OR REPLACE INTO task_history
               (id, platform, event_type, title, value, success, detail, data, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, platform, event_type, title, value, int(success), detail,
             json.dumps(data, ensure_ascii=False) if data else None, now),
        )
        await self._conn.commit()

    async def get_recent_tasks(self, limit: int = 50) -> list[dict]:
        """获取最近的任务记录。"""
        cursor = await self._conn.execute(
            "SELECT * FROM task_history ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_today_tasks(self) -> list[dict]:
        """获取今天的任务记录。"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cursor = await self._conn.execute(
            "SELECT * FROM task_history WHERE created_at LIKE ? ORDER BY created_at DESC",
            (f"{today}%",),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ================================
    # 账户状态
    # ================================
    async def update_account_state(
        self,
        platform: str,
        cookie_valid: bool = False,
        earnings: float = 0,
    ) -> None:
        """更新平台账户状态。"""
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """INSERT INTO account_state
               (platform, cookie_valid, last_active, today_earnings, total_earnings)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(platform) DO UPDATE SET
                   cookie_valid = excluded.cookie_valid,
                   last_active = excluded.last_active,
                   today_earnings = CASE
                       WHEN date(last_login) = date('now')
                       THEN today_earnings + excluded.today_earnings
                       ELSE excluded.today_earnings
                   END,
                   total_earnings = total_earnings + excluded.today_earnings
               """,
            (platform, int(cookie_valid), now, earnings, earnings),
        )
        await self._conn.commit()

    async def get_account_states(self) -> list[dict]:
        """获取所有平台账户状态。"""
        cursor = await self._conn.execute("SELECT * FROM account_state")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ================================
    # 统计
    # ================================
    async def get_today_earnings(self) -> float:
        """获取今日总收益。"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cursor = await self._conn.execute(
            "SELECT COALESCE(SUM(value), 0) FROM task_history "
            "WHERE created_at LIKE ? AND success = 1",
            (f"{today}%",),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0.0

    async def get_total_earnings(self) -> float:
        """获取累计总收益。"""
        cursor = await self._conn.execute(
            "SELECT COALESCE(SUM(value), 0) FROM task_history WHERE success = 1",
        )
        row = await cursor.fetchone()
        return row[0] if row else 0.0
