import asyncio
import json
import os
import time
from typing import Any

import aiosqlite

from .config import settings

DB_PATH = os.path.join(settings.data_dir, "chan.db")

_conn: aiosqlite.Connection | None = None
_lock = asyncio.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id          TEXT PRIMARY KEY,
    name        TEXT,
    fw          TEXT,
    last_seen   REAL,
    state_json  TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    device_id   TEXT NOT NULL,
    type        TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    script_json TEXT,
    status      TEXT NOT NULL,
    outcome     TEXT,
    error       TEXT,
    created_at  REAL NOT NULL,
    sent_at     REAL,
    finished_at REAL
);

CREATE INDEX IF NOT EXISTS idx_jobs_device ON jobs(device_id);
"""


async def init_db() -> None:
    global _conn
    os.makedirs(settings.data_dir, exist_ok=True)
    os.makedirs(os.path.join(settings.data_dir, "audio"), exist_ok=True)
    # isolation_level=None：每条语句自动提交。
    # 默认的 deferred 模式会让一次 SELECT 开启的事务一直挂着不结束，
    # 于是外部连接（比如运维脚本、测试）改过的数据在本进程里读出来是旧快照。
    _conn = await aiosqlite.connect(DB_PATH, isolation_level=None)
    _conn.row_factory = aiosqlite.Row
    await _conn.executescript(SCHEMA)
    # CREATE TABLE IF NOT EXISTS 不会给老库加列，这里补一次（只有一次成本）
    cur = await _conn.execute("PRAGMA table_info(jobs)")
    cols = {row[1] for row in await cur.fetchall()}
    if "outcome" not in cols:
        await _conn.execute("ALTER TABLE jobs ADD COLUMN outcome TEXT")
    # 2026-11-10：设备 hello 里本来就有 fw 版本号，之前只打了日志没存，
    # App 设置页要显示"固件版本"，这里补一列。
    cur = await _conn.execute("PRAGMA table_info(devices)")
    dcols = {row[1] for row in await cur.fetchall()}
    if "fw" not in dcols:
        await _conn.execute("ALTER TABLE devices ADD COLUMN fw TEXT")
    await _conn.commit()


async def close_db() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


def _db() -> aiosqlite.Connection:
    if _conn is None:
        raise RuntimeError("database not initialized")
    return _conn


async def upsert_device(
    device_id: str, name: str | None = None, fw: str | None = None
) -> None:
    async with _lock:
        await _db().execute(
            """
            INSERT INTO devices(id, name, fw, last_seen, state_json) VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = COALESCE(excluded.name, devices.name),
                fw = COALESCE(excluded.fw, devices.fw),
                last_seen = excluded.last_seen
            """,
            (device_id, name, fw, time.time(), "{}"),
        )
        await _db().commit()


async def touch_device(device_id: str) -> None:
    async with _lock:
        await _db().execute(
            "UPDATE devices SET last_seen = ? WHERE id = ?", (time.time(), device_id)
        )
        await _db().commit()


async def set_device_state(device_id: str, state: dict[str, Any]) -> None:
    async with _lock:
        await _db().execute(
            "UPDATE devices SET state_json = ?, last_seen = ? WHERE id = ?",
            (json.dumps(state, ensure_ascii=False), time.time(), device_id),
        )
        await _db().commit()


async def list_devices() -> list[dict[str, Any]]:
    async with _lock:
        cur = await _db().execute("SELECT * FROM devices ORDER BY last_seen DESC")
        rows = await cur.fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["state"] = json.loads(item.pop("state_json") or "{}")
        except json.JSONDecodeError:
            item["state"] = {}
        result.append(item)
    return result


async def create_job(
    job_id: str,
    device_id: str,
    job_type: str,
    payload: dict[str, Any],
    status: str = "pending",
) -> None:
    async with _lock:
        await _db().execute(
            """
            INSERT INTO jobs(id, device_id, type, payload_json, status, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                device_id,
                job_type,
                json.dumps(payload, ensure_ascii=False),
                status,
                time.time(),
            ),
        )
        await _db().commit()


async def get_active_timer(device_id: str) -> dict[str, Any] | None:
    """取该设备当前那一个计时器（同一时刻只允许一个）。"""
    async with _lock:
        cur = await _db().execute(
            """
            SELECT * FROM jobs
            WHERE device_id = ? AND type = 'timer' AND status IN ('running', 'overtime')
            ORDER BY created_at DESC LIMIT 1
            """,
            (device_id,),
        )
        row = await cur.fetchone()
    return _job_dict(row) if row else None


_ALLOWED_JOB_FIELDS = {"status", "outcome", "error", "script_json", "sent_at", "finished_at"}


async def update_job(job_id: str, **fields: Any) -> None:
    cols = [key for key in fields if key in _ALLOWED_JOB_FIELDS]
    if not cols:
        return
    values = [fields[key] for key in cols]
    values.append(job_id)
    sql = "UPDATE jobs SET " + ", ".join(f"{key} = ?" for key in cols) + " WHERE id = ?"
    async with _lock:
        await _db().execute(sql, values)
        await _db().commit()


async def get_job(job_id: str) -> dict[str, Any] | None:
    async with _lock:
        cur = await _db().execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cur.fetchone()
    if row is None:
        return None
    return _job_dict(row)


async def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    async with _lock:
        cur = await _db().execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cur.fetchall()
    return [_job_dict(row) for row in rows]


def _job_dict(row: aiosqlite.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ("payload_json", "script_json"):
        raw = item.pop(key, None)
        target = key.replace("_json", "")
        try:
            item[target] = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            item[target] = None
    return item


async def fail_active_jobs(device_id: str, reason: str) -> int:
    async with _lock:
        cur = await _db().execute(
            """
            UPDATE jobs
            SET status = 'error', error = ?, finished_at = ?
            WHERE device_id = ? AND status IN ('sent', 'running')
            """,
            (reason, time.time(), device_id),
        )
        await _db().commit()
        return cur.rowcount


async def pause_active_jobs(device_id: str, reason: str) -> int:
    """设备掉线：把进行中的 job 标成可重发，而不是直接判死。

    断线重发的语义是"从头再演一遍"——设备侧脚本是幂等的（同一份 script + 同一段音频），
    重放比丢一次表演更符合使用预期，所以这里只暂停不终结。
    """
    async with _lock:
        cur = await _db().execute(
            """
            UPDATE jobs
            SET status = 'pending_resend', error = ?
            WHERE device_id = ? AND status IN ('sent', 'running')
              AND type != 'timer'
            """,
            (reason, device_id),
        )
        await _db().commit()
        return cur.rowcount


async def list_resumable_jobs(
    device_id: str, since_ts: float
) -> list[dict[str, Any]]:
    """重连后要补发的 job：未终结、且不是很久以前创建的。

    `since_ts` 是时间窗下界——10 分钟前的任务重放没有意义（人早忘了上下文）。
    """
    async with _lock:
        cur = await _db().execute(
            """
            SELECT * FROM jobs
            WHERE device_id = ?
              AND status IN ('pending', 'pending_resend', 'sent', 'running')
              AND created_at >= ?
            ORDER BY created_at
            """,
            (device_id, since_ts),
        )
        rows = await cur.fetchall()
    return [_job_dict(row) for row in rows]


# ---------- 设置（key/value，值一律存 JSON） ----------
#
# 设计：只存"用户改过的项"，没改过就不写行，读取时由调用方补默认值。
# 这样部署升级后新增的默认值天然生效，不需要迁移脚本；也方便一次性清空回默认。


async def get_settings() -> dict[str, Any]:
    async with _lock:
        cur = await _db().execute("SELECT key, value_json FROM settings")
        rows = await cur.fetchall()
    out: dict[str, Any] = {}
    for row in rows:
        try:
            out[row["key"]] = json.loads(row["value_json"])
        except json.JSONDecodeError:
            continue
    return out


async def set_settings(values: dict[str, Any]) -> None:
    """整批写入。值为 None 表示删除该键（回到默认）。"""
    now = time.time()
    async with _lock:
        for key, value in values.items():
            if value is None:
                await _db().execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                await _db().execute(
                    """
                    INSERT INTO settings(key, value_json, updated_at) VALUES(?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value_json = excluded.value_json,
                        updated_at = excluded.updated_at
                    """,
                    (key, json.dumps(value, ensure_ascii=False), now),
                )
        await _db().commit()


# ---------- 维护 ----------


async def count_jobs() -> int:
    async with _lock:
        cur = await _db().execute("SELECT COUNT(*) AS n FROM jobs")
        row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def prune_jobs(keep: int) -> int:
    """只保留最近 `keep` 条**已结束**的记录，返回删除条数。

    活跃记录（pending/running/overtime/sent/pending_resend）永不删：把正在跑的
    计时器删掉会让状态机找不到 job，表现为计时器凭空消失。
    keep<=0 视为不清理——避免误传 0 把历史全删了。
    """
    if keep <= 0:
        return 0
    async with _lock:
        cur = await _db().execute(
            """
            DELETE FROM jobs
            WHERE status NOT IN ('pending', 'running', 'overtime', 'sent', 'pending_resend')
              AND id IN (
                SELECT id FROM jobs
                WHERE status NOT IN ('pending', 'running', 'overtime', 'sent', 'pending_resend')
                ORDER BY created_at DESC LIMIT -1 OFFSET ?
              )
            """,
            (keep,),
        )
        await _db().commit()
        return cur.rowcount

