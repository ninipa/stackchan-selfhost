import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from . import behavior, db, tts
from .config import settings
from .devices import DeviceConnection, registry
from .teleop import (
    VideoConnection,
    clear_photos,
    find_photo,
    handle_video_binary,
    handle_video_text,
    list_photos,
    video_registry,
)

logger = logging.getLogger("chan-behavior")

STATIC_DIR = Path(__file__).resolve().parent / "static"
AUDIO_NAME_RE = re.compile(r"^[0-9a-f]{16}\.ogg$")
# 页面版本：部署后前端比对 localStorage，发现不一致自动刷新（2026-11-12 旧页面缓存教训）
APP_VERSION = "2026-11-12.13"

# 行为控制面协议版本：加字段/改语义时 +1；旧固件没有 api_version 视为 0。
SUPPORTED_API_VERSION = 1
# 暂时接受旧固件（0）；新固件铺开后改成 1。
MIN_API_VERSION = 0


def _parse_api_version(hello: dict) -> int:
    try:
        return int(hello.get("api_version", 0) or 0)
    except (TypeError, ValueError):
        return 0


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    await db.init_db()
    logger.info(
        "chan-behavior started: public_base_url=%s voice=%s data_dir=%s",
        settings.public_base_url,
        settings.tts_voice,
        settings.data_dir,
    )
    # 预热固定台词：合成一次后写进 data/audio 缓存，之后播报零延迟（等于预生成）。
    # 失败不影响启动——真到那一刻会退化成"现合成"。
    for text in (FUSE_TEXT,):
        try:
            await tts.synth(text)
            logger.info("prompt warmed: %r", text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("prompt warm failed for %r: %s", text, exc)
    yield
    await db.close_db()


app = FastAPI(title="chan-behavior", lifespan=lifespan)

# 2026-11-10：允许跨源访问 API。
# 为什么需要：App 里可以手填 server 地址（局域网 IP / tailscale 域名），
# 页面是从其中一个源加载的，请求打到另一个源就是跨源。不给 CORS 的话
# 浏览器会静默拦掉，表现成"填了地址还是连不上"，很难排查。
# 安全边界不在这里——靠 X-App-Token，以及"8100 只出现在内网/tailnet"这条部署纪律。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    repeat: int = Field(default=1, ge=1, le=settings.max_repeat)
    gap_ms: int | None = Field(default=None, ge=0, le=10000)
    device_id: str | None = None
    # 测试用：指定动作序列，如 ["nod"] 只做点头；不传按默认循环
    beats: list[str] | None = None


class TimerRequest(BaseModel):
    duration_ms: int = Field(ge=5000, le=4 * 3600 * 1000)
    label: str = Field(default="专注", max_length=20)
    task: str = Field(default="", max_length=60)
    device_id: str | None = None


# 剩余多少比例时切到全屏引线（用户定：20%，不按总长封顶）
FUSE_RATIO = 0.2
# 进入引线时那句提醒：固定文本 → 容器启动时预热一次，之后永远是缓存命中（等于预生成）
FUSE_TEXT = "要抓紧了哟"
# 进引线时剩余不足这么久就不播：话还没说完就到点了，反而更乱
FUSE_MIN_LEAD_MS = 4000
# 到点后多久之内点「结束」算按时完成（用户定 2 分钟）
ON_TIME_WINDOW_MS = 2 * 60 * 1000
# 超时多久自动归档（与设备收起 HUD 的 10 分钟对齐）
EXPIRE_AFTER_MS = 10 * 60 * 1000
# 服务端只做"算状态"，不做后台定时器：phase 全部由 started_at + duration 推出来，
# 容器重启也不会丢——这一点和 message job 完全不同，见 docs/pomodoro-state-matrix-2026-11-08.md


def _timer_view(job: dict) -> dict:
    """按墙上时间算当前处于哪个阶段。设备只信这个结果，不自己算历史。"""
    payload = job["payload"] or {}
    total = int(payload.get("duration_ms", 0))
    elapsed = max(0.0, (time.time() - float(job["created_at"])) * 1000)
    overtime = max(0.0, elapsed - total)
    remaining = max(0.0, total - elapsed)
    if total <= 0:
        phase = "done"
    elif elapsed >= total:
        phase = "overtime"
    elif remaining <= total * FUSE_RATIO:
        phase = "fuse"
    else:
        phase = "focus"
    return {
        "type": "timer_start",
        "job_id": job["id"],
        "phase": phase,
        "duration_ms": total,
        "remaining_ms": int(remaining),
        "overtime_ms": int(overtime),
        "label": payload.get("label", "专注"),
        "task": payload.get("task", ""),
    }


async def require_app_token(
    x_app_token: str | None = Header(default=None, alias="X-App-Token"),
) -> None:
    if not x_app_token or x_app_token != settings.app_token:
        raise HTTPException(status_code=401, detail="invalid app token")


def _spoken_task(task: str, limit: int = 12) -> str:
    """播报用的任务名：截到 12 字。

    屏幕上可以显示全名（20 字上限），但念出来 20 字要将近 4 秒，
    提醒听起来会像一段独白。超过就截断，不加省略号（念不出来）。
    """
    task = (task or "").strip()
    return task[:limit]


async def _advance_timer(job: dict) -> dict:
    """按墙上时间推进计时状态，返回最新记录。

    running 过了 0 点 → overtime；overtime 超过 10 分钟 → expired。
    **不能只等设备上报 timer_done**：设备掉线时没人报，状态会永远卡在 running，
    App 上就会看到一段"永远在跑"的计时。所以每次读都顺手推一次。
    """
    if job.get("type") != "timer":
        return job
    view = _timer_view(job)
    # 两段判断必须顺序执行、不能 return：设备掉线很久时可能一次就要从 running
    # 直接推到 expired，中间状态（overtime）也要落库，但不该中断这次推进。
    if job["status"] == "running" and view["overtime_ms"] > 0:
        await db.update_job(job["id"], status="overtime")
        logger.info("timer %s -> overtime (%.0fs past due)", job["id"], view["overtime_ms"] / 1000)
        job = await db.get_job(job["id"]) or job
    if job["status"] == "overtime" and view["overtime_ms"] >= EXPIRE_AFTER_MS:
        await db.update_job(
            job["id"], status="expired", outcome="expired", finished_at=time.time()
        )
        logger.info("timer %s -> expired (无人处理 %.0f 分钟)", job["id"], view["overtime_ms"] / 60000)
        job = await db.get_job(job["id"]) or job
    return job


def _finish_outcome(job: dict) -> str | None:
    """决定这一次「结束」算哪种结局。None = 当前状态不该被结束。"""
    if job["status"] == "running":
        return "early"          # 还没到点就收工：提前完成
    if job["status"] == "overtime":
        view = _timer_view(job)
        return "on_time" if view["overtime_ms"] <= ON_TIME_WINDOW_MS else "late"
    return None


# 断线重发的时间窗：超过这个时间的旧 job 不再补发（重放没有语境意义）
RESUME_WINDOW_SECONDS = 600
CHUNK_SIZE = 2048


async def _dispatch_job(
    conn: DeviceConnection,
    job_id: str,
    script: dict,
    audio_bytes: bytes,
    audio_id: str,
) -> None:
    """把一段音频 + 行为脚本推给设备。首次下发与断线重发共用同一条路径。"""
    await conn.send_json(
        {
            "type": "audio_begin",
            "job_id": job_id,
            "audio_id": audio_id,
            "len": len(audio_bytes),
        }
    )
    for offset in range(0, len(audio_bytes), CHUNK_SIZE):
        await conn.send_bytes(audio_bytes[offset : offset + CHUNK_SIZE])
    await conn.send_json({"type": "audio_end", "job_id": job_id, "audio_id": audio_id})
    await conn.send_json({"type": "behavior", "script": script})


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse("/app")


@app.get("/app")
async def app_page() -> FileResponse:
    # 不许缓存：手机浏览器会把整个页面缓存住，改了文案/按钮还显示旧版，
    # 排查时会以为是后端没生效（2026-11-08 踩过：超时后看不到「我知道了」）。
    return FileResponse(
        STATIC_DIR / "index.html", headers={"Cache-Control": "no-store, must-revalidate"}
    )


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "devices_online": len(registry.list())}


@app.get("/api/devices")
async def api_devices(_: None = Depends(require_app_token)) -> list[dict]:
    rows = await db.list_devices()
    online = {conn.device_id for conn in registry.list()}
    for row in rows:
        row["online"] = row["id"] in online
    return rows


@app.post("/api/devices/{device_id}/screenshot")
async def api_device_screenshot(
    device_id: str,
    panel: bool = False,
    _: None = Depends(require_app_token),
) -> dict:
    """让设备抓一帧屏幕，通过 WS 分块传回来，落到 data/snapshots/last.raw。

    panel=true 时先让设备打开番茄钟抽屉再抓——抽屉是长按触发的，
    没有真手指时靠这个自测布局。
    """
    conn = registry.get(device_id)
    if conn is None:
        raise HTTPException(status_code=409, detail="device offline")
    if panel:
        await conn.send_json({"type": "panel"})
        # 设备处理 panel 消息要排到主循环的队列里，实测约 1.2 秒才真正画出来；
        # 0.4 秒就发 snapshot 的话，抓到的是抽屉出现前的画面。
        await asyncio.sleep(1.5)
    await conn.send_json({"type": "snapshot"})
    logger.info("screenshot requested for %s (panel=%s)", device_id, panel)
    return {"ok": True}


# ---------- 设置 ----------
#
# 只暴露"改完立刻能验证"的项。没在这里的一律不开放写，原因分三类：
#   1. 要改 .config.yaml 并重启 xiaozhi 容器（主对话音色、LLM 模型、提示词）
#   2. 要新消息类型 + 重烧固件（亮度、音量、时区、自动关机、姿态常量、唤醒词）
#   3. 必须在设备上确认才安全（解绑、恢复出厂、舵机零点）
# 这三类统一走 readonly 展示，用户看得到当前值，改要去对应的地方。

SETTING_SPEC: dict[str, dict] = {
    "history_keep": {
        "type": "int",
        "min": 20,
        "max": 5000,
        "default": settings.history_keep,
        "label": "历史保留条数",
    },
}


def _read_xiaozhi_voice() -> str | None:
    """从只读挂载的 .config.yaml 里取主对话音色。

    只取 `TTS.EdgeTTS.voice` 这一个值：那个文件里有 LLM/视觉模型的 API key，
    不能整份吐给前端。结构对不上就返回 None（前端显示"未知"），不报错——
    上游改配置结构不该把设置页打挂。
    """
    try:
        text = Path(settings.xiaozhi_config).read_text(encoding="utf-8")
    except OSError:
        return None
    section = None
    sub = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        indent = len(line) - len(line.lstrip(" "))
        key = stripped.split(":", 1)[0].strip()
        if indent == 0:
            section, sub = key, None
            continue
        if section != "TTS":
            continue
        if indent <= 2:
            sub = key
        elif sub == "EdgeTTS" and key == "voice":
            return stripped.split(":", 1)[1].strip().strip("'\"") or None
    return None


def _setting_values(stored: dict) -> dict:
    """DB 里有的用 DB，没有的补默认值。"""
    return {key: stored.get(key, spec["default"]) for key, spec in SETTING_SPEC.items()}


@app.get("/api/settings")
async def api_get_settings(_: None = Depends(require_app_token)) -> dict:
    stored = await db.get_settings()
    return {
        "values": _setting_values(stored),
        "spec": {
            key: {k: v for k, v in spec.items() if k != "label"} | {"label": spec["label"]}
            for key, spec in SETTING_SPEC.items()
        },
        "readonly": {
            # 改不动的值：显示出来，附上"去哪儿改"，比藏起来好。
            "xiaozhi_voice": _read_xiaozhi_voice(),
            "behavior_voice": settings.tts_voice,
            "xiaozhi_config": settings.xiaozhi_config,
        },
    }


@app.post("/api/settings")
async def api_set_settings(
    payload: dict, _: None = Depends(require_app_token)
) -> dict:
    """只接受 SETTING_SPEC 里的键；传 null 表示恢复默认。范围外的键直接忽略。"""
    updates: dict = {}
    for key, value in (payload or {}).items():
        spec = SETTING_SPEC.get(key)
        if spec is None:
            continue
        if value is None:
            updates[key] = None
            continue
        if spec["type"] == "int":
            try:
                num = int(value)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"{key} 需要整数")
            if not (spec["min"] <= num <= spec["max"]):
                raise HTTPException(
                    status_code=400,
                    detail=f"{key} 需在 {spec['min']}–{spec['max']} 之间",
                )
            updates[key] = num
    if updates:
        await db.set_settings(updates)
        logger.info("settings updated: %s", ", ".join(updates))
    stored = await db.get_settings()
    return {"values": _setting_values(stored)}


# ---------- 维护 ----------


async def _prune_history_safe() -> None:
    """超过保留条数就裁一次。

    在每次创建 job 之后异步触发：这样"保留 N 条"是**持续成立**的上限，
    而不是"下次有人点清理才算数"的参数。失败只记日志——清理绝不能拖累
    消息下发和番茄钟这两个主流程。
    """
    try:
        stored = await db.get_settings()
        keep = int(_setting_values(stored)["history_keep"])
        deleted = await db.prune_jobs(keep)
        if deleted:
            logger.info("history pruned: %d removed (keep=%d)", deleted, keep)
    except Exception as exc:  # noqa: BLE001
        logger.warning("history prune failed: %s", exc)


def _audio_stats() -> tuple[int, int]:
    audio_dir = Path(settings.data_dir) / "audio"
    count = 0
    total = 0
    for path in audio_dir.glob("*.ogg"):
        try:
            total += path.stat().st_size
            count += 1
        except OSError:
            continue
    return count, total


@app.get("/api/maintenance/stats")
async def api_stats(_: None = Depends(require_app_token)) -> dict:
    audio_count, audio_bytes = _audio_stats()
    db_bytes = 0
    try:
        db_bytes = Path(settings.data_dir, "chan.db").stat().st_size
    except OSError:
        pass
    stored = await db.get_settings()
    return {
        "jobs": await db.count_jobs(),
        "db_bytes": db_bytes,
        "audio_files": audio_count,
        "audio_bytes": audio_bytes,
        "history_keep": _setting_values(stored)["history_keep"],
    }


class CleanupRequest(BaseModel):
    history: bool = False
    audio: bool = False


@app.post("/api/maintenance/cleanup")
async def api_cleanup(
    req: CleanupRequest, _: None = Depends(require_app_token)
) -> dict:
    """手动清理。不做后台定时清理——这个服务的所有动作都由用户触发或设备上报驱动。"""
    jobs_deleted = 0
    audio_deleted = 0
    if req.history:
        stored = await db.get_settings()
        keep = _setting_values(stored)["history_keep"]
        jobs_deleted = await db.prune_jobs(int(keep))
    if req.audio:
        # 只删 24 小时没被碰过的：正在播的、刚合成还没发完的一定在窗口内。
        cutoff = time.time() - 24 * 3600
        audio_dir = Path(settings.data_dir) / "audio"
        for path in audio_dir.glob("*.ogg"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    audio_deleted += 1
            except OSError:
                continue
    logger.info("cleanup: %d job(s), %d audio file(s)", jobs_deleted, audio_deleted)
    return {"jobs_deleted": jobs_deleted, "audio_deleted": audio_deleted}


@app.get("/api/jobs")
async def api_jobs(
    limit: int = 20, _: None = Depends(require_app_token)
) -> list[dict]:
    """最近的 job 列表，用于 App 的「历史」页。"""
    jobs = await db.list_jobs(limit=max(1, min(limit, 100)))
    # 列表里顺手纠正显示状态：一直 running 但早就过 0 的，历史页应显示「超时中」
    # 而不是「进行中」。只改返回值不写库——真正的状态推进在 get_job / timers/active。
    for job in jobs:
        if job.get("type") == "timer" and job["status"] == "running":
            view = _timer_view(job)
            if view["overtime_ms"] > 0:
                job["status"] = (
                    "expired" if view["overtime_ms"] >= EXPIRE_AFTER_MS else "overtime"
                )
    return jobs


@app.get("/api/jobs/{job_id}")
async def api_job(job_id: str, _: None = Depends(require_app_token)) -> dict:
    job = await db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return await _advance_timer(job)


@app.post("/api/jobs/{job_id}/cancel")
async def api_cancel_job(
    job_id: str, _: None = Depends(require_app_token)
) -> dict:
    """取消一个还没结束的 job。

    先落库再通知设备：设备可能已经离线，状态以服务端为准，重连时不会再补发它。
    """
    job = await db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] in ("done", "error", "canceled", "expired"):
        # 计时已经结束，但设备上的 HUD 可能还挂着：允许 App 发一次清除
        if job["type"] == "timer":
            conn = registry.get(job["device_id"])
            if conn is not None:
                try:
                    await conn.send_json({"type": "timer_cancel", "job_id": job_id})
                except Exception as exc:  # noqa: BLE001
                    logger.warning("timer hud clear failed for %s: %s", job_id, exc)
        return job

    await db.update_job(job_id, status="canceled", finished_at=time.time())
    conn = registry.get(job["device_id"])
    if conn is not None:
        try:
            # timer 用独立的类型，设备据此清掉信息带/引线；message 的 cancel 语义不变
            kind = "timer_cancel" if job["type"] == "timer" else "cancel"
            await conn.send_json({"type": kind, "job_id": job_id})
        except Exception as exc:  # noqa: BLE001
            logger.warning("cancel notify failed for %s: %s", job_id, exc)
    logger.info("job %s canceled by app", job_id)
    canceled = await db.get_job(job_id)
    assert canceled is not None
    return canceled


@app.post("/api/timers")
async def api_timer_create(
    req: TimerRequest, _: None = Depends(require_app_token)
) -> dict:
    """起一段番茄钟。

    同一设备同时只允许一个（409），因为设备上只有一个信息带/一个引线，
    两个计时器同时跑没法表达，也必然是 Bug。
    """
    conn = registry.get(req.device_id) if req.device_id else registry.first_online()
    if conn is None:
        raise HTTPException(status_code=409, detail="device offline")

    existing = await db.get_active_timer(conn.device_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="timer already running")

    job_id = f"tmr-{uuid.uuid4().hex[:8]}"
    payload = {
        "duration_ms": req.duration_ms,
        "label": req.label,
        "task": req.task,
    }
    await db.create_job(job_id, conn.device_id, "timer", payload, status="running")
    job = await db.get_job(job_id)
    assert job is not None
    view = _timer_view(job)
    await conn.send_json(view)
    logger.info(
        "timer %s started on %s: %s %dms (%r)",
        job_id,
        conn.device_id,
        req.label,
        req.duration_ms,
        req.task[:30],
    )
    return {"job": job, "timer": view}


@app.get("/api/timers/active")
async def api_timer_active(
    device_id: str | None = None, _: None = Depends(require_app_token)
) -> dict:
    target = device_id
    if target is None:
        first = registry.first_online()
        target = first.device_id if first else None
    if target is None:
        raise HTTPException(status_code=404, detail="no device")
    job = await db.get_active_timer(target)
    if job is None:
        return {"timer": None}
    # 顺手推进：到点了就转 overtime，App 才能继续显示「超时」而不是突然消失
    job = await _advance_timer(job)
    if job["status"] not in ("running", "overtime"):
        return {"timer": None}
    return {"timer": _timer_view(job)}


@app.post("/api/jobs/{job_id}/finish")
async def api_finish_job(
    job_id: str, _: None = Depends(require_app_token)
) -> dict:
    """用户主动结束一次专注。

    三种结局，按点下去的时刻判：
    - 还没到点 → `early`（提前完成）
    - 到点 2 分钟内 → `on_time`（按时收工）
    - 超过 2 分钟 → `late`（超时收工）

    这是「完成」，和 `cancel` 的「放弃」是两回事：前者计入完成数，后者不计。
    """
    job = await db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["type"] != "timer":
        raise HTTPException(status_code=400, detail="only timer jobs can be finished")

    job = await _advance_timer(job)
    if job["status"] in ("done", "canceled", "expired"):
        return job  # 幂等：重复点不报错，也不改结局

    outcome = _finish_outcome(job)
    if outcome is None:
        return job

    await db.update_job(
        job_id, status="done", outcome=outcome, finished_at=time.time()
    )
    logger.info("timer %s finished by app: %s", job_id, outcome)

    conn = registry.get(job["device_id"])
    if conn is not None:
        try:
            if outcome == "early":
                # 提前完成：让设备演一遍"完成"（点头一次 + 绿闪 + 气泡）
                await conn.send_json({"type": "timer_complete", "job_id": job_id})
                asyncio.create_task(_announce(conn, job, _finished_text(job), "done"))
            else:
                # 超时后收工：不表演，只把红字收掉；声音仍然给（和面板完成保持一致）
                await conn.send_json({"type": "timer_cancel", "job_id": job_id})
                asyncio.create_task(_announce(conn, job, _finished_text(job), "done"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("finish notify failed for %s: %s", job_id, exc)

    finished = await db.get_job(job_id)
    assert finished is not None
    return finished


@app.post("/api/messages")
async def api_message(
    req: MessageRequest, _: None = Depends(require_app_token)
) -> dict:
    conn = registry.get(req.device_id) if req.device_id else registry.first_online()
    if conn is None:
        raise HTTPException(status_code=409, detail="device offline")

    job_id = f"msg-{uuid.uuid4().hex[:8]}"
    await db.create_job(job_id, conn.device_id, "message", req.model_dump())

    try:
        key = await tts.synth(req.text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("tts failed for job %s", job_id)
        await db.update_job(
            job_id, status="error", error=f"tts: {exc}", finished_at=time.time()
        )
        raise HTTPException(status_code=502, detail=f"tts failed: {exc}") from exc

    gap_ms = req.gap_ms if req.gap_ms is not None else settings.default_gap_ms
    try:
        audio_bytes = Path(tts.audio_path(key)).read_bytes()
    except OSError as exc:
        logger.exception("read audio failed for job %s", job_id)
        await db.update_job(
            job_id, status="error", error=f"audio_read: {exc}", finished_at=time.time()
        )
        raise HTTPException(status_code=500, detail=f"audio read failed: {exc}") from exc

    script = behavior.build_message_script(
        job_id,
        req.text,
        tts.audio_url(key),
        key,
        len(audio_bytes),
        req.repeat,
        gap_ms,
        req.beats,
    )
    await db.update_job(
        job_id,
        status="sent",
        script_json=json.dumps(script, ensure_ascii=False),
        sent_at=time.time(),
    )

    try:
        # 音频走控制 WS 分块下发，设备不再新开 HTTP socket
        await _dispatch_job(conn, job_id, script, audio_bytes, key)
    except Exception as exc:  # noqa: BLE001
        logger.exception("send failed for job %s", job_id)
        await db.update_job(
            job_id, status="error", error=f"send: {exc}", finished_at=time.time()
        )
        raise HTTPException(status_code=502, detail=f"device send failed: {exc}") from exc

    logger.info(
        "job %s queued for %s: %r x%d (%.0fms gap, audio %d bytes)",
        job_id,
        conn.device_id,
        req.text[:40],
        req.repeat,
        gap_ms,
        len(audio_bytes),
    )
    job = await db.get_job(job_id)
    assert job is not None
    return job


@app.get("/audio/{name}")
async def audio(name: str) -> FileResponse:
    if not AUDIO_NAME_RE.match(name):
        raise HTTPException(status_code=404, detail="not found")
    path = os.path.join(settings.data_dir, "audio", name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type="audio/ogg")


@app.websocket("/device/ws")
async def device_ws(ws: WebSocket, token: str = "") -> None:
    if token != settings.device_token:
        await ws.close(code=4401)
        return

    await ws.accept()
    try:
        hello = await asyncio.wait_for(
            ws.receive_json(), timeout=settings.device_hello_timeout
        )
    except Exception:  # noqa: BLE001
        await ws.close(code=4400)
        return

    if hello.get("type") != "hello" or not hello.get("device_id"):
        await ws.close(code=4400)
        return

    device_id = str(hello["device_id"])
    api_version = _parse_api_version(hello)
    if api_version < MIN_API_VERSION:
        logger.warning(
            "device %s rejected: api_version=%s < %s",
            device_id,
            api_version,
            MIN_API_VERSION,
        )
        try:
            await ws.send_json(
                {
                    "type": "hello_error",
                    "reason": "api_version_too_old",
                    "min": MIN_API_VERSION,
                }
            )
        except Exception:  # noqa: BLE001
            pass
        await ws.close(code=4426)
        return
    if api_version < SUPPORTED_API_VERSION:
        logger.warning(
            "device %s legacy api_version=%s (server supports %s)",
            device_id,
            api_version,
            SUPPORTED_API_VERSION,
        )
    conn = DeviceConnection(
        device_id=device_id,
        ws=ws,
        name=hello.get("name"),
        state=hello.get("state") or {},
    )
    registry.register(conn)
    await db.upsert_device(device_id, conn.name, hello.get("fw"))
    await db.set_device_state(device_id, conn.state)
    await conn.send_json(
        {
            "type": "welcome",
            "server": "chan-behavior",
            "api_version": SUPPORTED_API_VERSION,
            "ts": time.time(),
        }
    )

    logger.info(
        "device %s connected (fw=%s api_version=%s)",
        device_id,
        hello.get("fw"),
        api_version,
    )

    # 断线重发：把掉线时暂停的 job 补发一遍（10 分钟以内的）
    await _resume_jobs(conn)
    # 番茄钟不重放，只对表：把服务端算出的剩余时间下发，设备按它续跑
    await _sync_timer(conn)

    try:
        while True:
            raw = await ws.receive()
            if raw.get("bytes") is not None:
                await _handle_device_binary(conn, raw["bytes"])
            elif raw.get("text") is not None:
                await _handle_device_message(conn, json.loads(raw["text"]))
    except WebSocketDisconnect:
        logger.info("device %s disconnected", device_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("device %s socket error: %s", device_id, exc)
    finally:
        # 只注销自己：设备抖动时会短暂并存两条连接，后来者覆盖了 registry，
        # 旧连接退出时若无条件 unregister，会把活着的那条也删掉
        # （现象：设备还在发心跳，但 /api/devices 显示 offline，消息发不出去）
        if registry.get(device_id) is conn:
            registry.unregister(device_id)
        # 断线不再直接判死：标成待重发，设备回来时补演一遍（见 _resume_jobs）
        paused = await db.pause_active_jobs(device_id, "device_disconnected")
        if paused:
            logger.warning(
                "device %s disconnected; %d active job(s) paused for resend",
                device_id,
                paused,
            )


async def _resume_jobs(conn: DeviceConnection) -> None:
    """重连后补发未完成的 job。

    音频不落库、只落文件，所以这里按 script 里的 audio_id 从磁盘重读；
    文件丢了就放弃这条 job（判 error），不让它一直挂在 pending_resend。
    """
    jobs = await db.list_resumable_jobs(
        conn.device_id, time.time() - RESUME_WINDOW_SECONDS
    )
    for job in jobs:
        # timer 不走重放：它有自己的 _sync_timer（重放会导致 25 分钟从头再来）
        if job.get("type") == "timer":
            continue
        script = job.get("script")
        if not script:
            continue
        audio_id = next(
            (
                step.get("audio_id")
                for step in script.get("steps", [])
                if step.get("op") == "say" and step.get("audio_id")
            ),
            None,
        )
        if not audio_id:
            continue
        try:
            audio_bytes = Path(tts.audio_path(audio_id)).read_bytes()
        except OSError as exc:
            logger.warning("resume %s failed (audio %s): %s", job["id"], audio_id, exc)
            await db.update_job(
                job["id"],
                status="error",
                error="audio_lost",
                finished_at=time.time(),
            )
            continue

        try:
            await _dispatch_job(conn, job["id"], script, audio_bytes, audio_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("resume %s send failed: %s", job["id"], exc)
            continue

        await db.update_job(job["id"], status="sent", sent_at=time.time(), error=None)
        logger.info("job %s resent to %s after reconnect", job["id"], conn.device_id)


async def _handle_device_binary(conn: DeviceConnection, data: bytes) -> None:
    if conn.snapshot_buf is None:
        return
    conn.snapshot_buf += data


async def _save_snapshot(conn: DeviceConnection) -> None:
    """把攒好的 RGB565 原样落盘，转 PNG 留给主机侧（容器里没有 PIL）。"""
    buf = conn.snapshot_buf or bytearray()
    meta = dict(conn.snapshot_meta)
    conn.snapshot_buf = None
    want = meta.get("len") or 0
    out_dir = Path(settings.data_dir) / "snapshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    if want and len(buf) != want:
        logger.warning("snapshot from %s incomplete: %d/%d", conn.device_id, len(buf), want)
        return
    (out_dir / "last.raw").write_bytes(bytes(buf))
    (out_dir / "last.json").write_text(
        json.dumps({**meta, "device_id": conn.device_id, "got": len(buf)})
    )
    logger.info("snapshot saved: %dx%d %d bytes", meta.get("w"), meta.get("h"), len(buf))


async def _sync_timer(conn: DeviceConnection) -> None:
    """重连对表：设备侧只做单调递减，服务端是唯一权威。"""
    job = await db.get_active_timer(conn.device_id)
    if job is None:
        # 设备掉线期间可能被取消/结束了，但本机还在跑。不发这一条，HUD 会一直挂到
        # 本地倒计时走完（甚至走到 -10 分钟）。设备没有计时时收到它是无害的空操作。
        await conn.send_json({"type": "timer_cancel"})
        return
    job = await _advance_timer(job)
    if job["status"] not in ("running", "overtime"):
        await conn.send_json({"type": "timer_cancel", "job_id": job["id"]})
        return
    view = _timer_view(job)
    await conn.send_json(view)
    logger.info(
        "timer %s synced to %s: phase=%s remaining=%dms overtime=%dms status=%s",
        job["id"],
        conn.device_id,
        view["phase"],
        view["remaining_ms"],
        view["overtime_ms"],
        job["status"],
    )


async def _handle_device_message(conn: DeviceConnection, msg: dict) -> None:
    typ = msg.get("type")
    conn.last_seen = time.time()

    if typ == "heartbeat":
        await db.touch_device(conn.device_id)
    elif typ == "job_started":
        await _update_job_if_owned(conn, msg.get("job_id"), status="running")
    elif typ == "job_done":
        await _update_job_if_owned(
            conn, msg.get("job_id"), status="done", finished_at=time.time()
        )
    elif typ == "job_error":
        await _update_job_if_owned(
            conn,
            msg.get("job_id"),
            status="error",
            error=str(msg.get("error") or "device_error")[:500],
            finished_at=time.time(),
        )
    elif typ == "state":
        state = msg.get("state") or {}
        if isinstance(state, dict):
            conn.state = state
            await db.set_device_state(conn.device_id, state)
    elif typ == "snapshot_begin":
        conn.snapshot_meta = {
            "w": int(msg.get("w") or 0),
            "h": int(msg.get("h") or 0),
            "len": int(msg.get("len") or 0),
            "ts": time.time(),
        }
        conn.snapshot_buf = bytearray()
    elif typ == "snapshot_end":
        await _save_snapshot(conn)
    elif typ == "timer_done":
        # 设备报"到点并演完了"。注意这里**不置 done**：到点只是进入 overtime，
        # 用户还没表态（要么点完成/我知道了，要么 10 分钟后自动归档）。
        # 早先直接置 done，导致 App 立刻认为"没有进行中的任务"，红字 HUD 也退不出去。
        job = await db.get_job(str(msg.get("job_id") or ""))
        if job is not None and job["device_id"] == conn.device_id:
            job = await _advance_timer(job)
            view = _timer_view(job)
            logger.info(
                "timer %s hit zero on %s (overtime=%dms, status=%s)",
                job["id"],
                conn.device_id,
                view["overtime_ms"],
                job["status"],
            )
            # 设备已经做了视觉表演（点头/绿闪/气泡），这里补一句人声
            asyncio.create_task(_announce(conn, job, _done_text(job)))
    elif typ == "timer_fuse":
        # 设备进入 20% 引线阶段（只在 0→1 那一次上报，重连补发不会重复）
        job = await db.get_job(str(msg.get("job_id") or ""))
        if job is not None and job["device_id"] == conn.device_id:
            job = await _advance_timer(job)
            view = _timer_view(job)
            if job["status"] == "running" and view["remaining_ms"] >= FUSE_MIN_LEAD_MS:
                logger.info(
                    "timer %s entered fuse (remain %dms), announcing",
                    job["id"],
                    view["remaining_ms"],
                )
                asyncio.create_task(_announce(conn, job, FUSE_TEXT, "fuse"))
            else:
                logger.info(
                    "timer %s fuse announce skipped (status=%s remain=%dms)",
                    job["id"],
                    job["status"],
                    view["remaining_ms"],
                )
    elif typ == "timer_finished":
        # 设备上的长按面板点了「完成」：设备已经演过一遍（气泡 + 点头），
        # 这里补状态 + 一句人声——否则面板完成只有气泡、没有声音，和到点不一致。
        job = await db.get_job(str(msg.get("job_id") or ""))
        if job is not None and job["device_id"] == conn.device_id:
            job = await _advance_timer(job)
            outcome = _finish_outcome(job)
            if outcome is None:
                logger.info("timer %s already closed (%s)", job["id"], job["status"])
                return
            await db.update_job(
                job["id"], status="done", outcome=outcome, finished_at=time.time()
            )
            logger.info("timer %s finished on device: %s", job["id"], outcome)
            asyncio.create_task(_announce(conn, job, _finished_text(job), "done"))
    elif typ == "timer_abandon":
        # 设备上的长按面板点了「放弃任务」：设备已经自己收了 HUD，这里只落状态
        job = await db.get_job(str(msg.get("job_id") or ""))
        if job is not None and job["device_id"] == conn.device_id:
            if job["status"] in ("running", "overtime"):
                await db.update_job(
                    job["id"], status="canceled", finished_at=time.time()
                )
                logger.info("timer %s abandoned on device", job["id"])
    else:
        logger.debug("device %s unknown message type: %s", conn.device_id, typ)


async def _announce(conn: DeviceConnection, job: dict, text: str, suffix: str = "say") -> None:
    """让机器人补一句人声（到点：「任务名，时间到」；完成：「任务名，完成了」；引线：「要抓紧了哟」）。

    合成需要 1~2 秒，正好和设备的点头表演错开；失败就静默——屏幕上还有气泡。
    固定台词（引线那句）在启动时已预热，这里直接命中缓存。
    """
    if not text:
        return

    try:
        key = await tts.synth(text)
        audio = Path(tts.audio_path(key)).read_bytes()
    except Exception as exc:  # noqa: BLE001
        logger.warning("timer announce failed: %s", exc)
        return

    say_id = f"{job['id']}-{suffix}"   # 独立 id：不碰计时那条记录的状态
    script = {
        "job_id": say_id,
        "steps": [
            {
                "op": "say",
                "text": text,
                "audio": tts.audio_url(key),
                "audio_id": key,
                "audio_len": len(audio),
                "repeat": 1,
                "gap_ms": 0,
            }
        ],
    }
    try:
        await _dispatch_job(conn, say_id, script, audio, key)
        logger.info("timer announce sent to %s: %r", conn.device_id, text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("timer announce dispatch failed: %s", exc)


def _done_text(job: dict) -> str:
    task = _spoken_task((job.get("payload") or {}).get("task") or "")
    return f"{task}，时间到" if task else "时间到"


def _finished_text(job: dict) -> str:
    task = _spoken_task((job.get("payload") or {}).get("task") or "")
    return f"{task}，完成了" if task else "完成了"


async def _update_job_if_owned(
    conn: DeviceConnection, job_id: str | None, **fields
) -> None:
    if not job_id:
        return
    job = await db.get_job(str(job_id))
    if job is None or job["device_id"] != conn.device_id:
        # 播报用的 <timer>-say / -fuse / -done 本来就不入库，不当作异常
        if str(job_id).rsplit("-", 1)[-1] in ("say", "fuse", "done"):
            logger.debug("device %s ack for announce job %s", conn.device_id, job_id)
        else:
            logger.warning("device %s reported unknown job %s", conn.device_id, job_id)
        return
    await db.update_job(str(job_id), **fields)
    logger.info("job %s -> %s", job_id, fields.get("status"))


# --------------------------------------------------------------------------- #
# 遥控台（teleop）：控制面转发 + 视频通道 + 相册
#
# 控制面复用 /device/ws 的 teleop 消息；视频走独立的 /device/video，
# 与控制面隔离——视频链接抖动不会影响舵机指令（spike 的教训：别把视频塞进控制 WS）。
# --------------------------------------------------------------------------- #


@app.get("/api/app_version")
async def api_app_version() -> dict:
    return {"version": APP_VERSION}


class TeleopDeviceRequest(BaseModel):
    device_id: str


class TeleopMoveRequest(BaseModel):
    device_id: str
    yaw: float = Field(..., ge=-90.0, le=90.0)    # 绝对角度（度）
    pitch: float = Field(..., ge=5.0, le=85.0)    # 绝对角度（度）
    speed: int = Field(default=600, ge=100, le=1000)


async def _teleop_send(device_id: str, payload: dict) -> None:
    conn = registry.get(device_id)
    if conn is None:
        raise HTTPException(status_code=409, detail="device offline")
    await conn.send_json(payload)


@app.post("/api/teleop/start")
async def api_teleop_start(
    req: TeleopDeviceRequest, _: None = Depends(require_app_token)
) -> dict:
    await _teleop_send(req.device_id, {"type": "teleop", "start": True})
    logger.info("teleop session start -> %s", req.device_id)
    return {"ok": True}


@app.post("/api/teleop/move")
async def api_teleop_move(
    req: TeleopMoveRequest, _: None = Depends(require_app_token)
) -> dict:
    await _teleop_send(
        req.device_id,
        {"type": "teleop", "yaw": req.yaw, "pitch": req.pitch, "speed": req.speed},
    )
    return {"ok": True}


@app.post("/api/teleop/end")
async def api_teleop_end(req: TeleopDeviceRequest, _: None = Depends(require_app_token)) -> dict:
    await _teleop_send(req.device_id, {"type": "teleop", "end": True})
    logger.info("teleop session end -> %s", req.device_id)
    return {"ok": True}


@app.post("/api/teleop/keepalive")
async def api_teleop_keepalive(
    req: TeleopDeviceRequest, _: None = Depends(require_app_token)
) -> dict:
    """页面开着时的保活：设备失联判定只依赖它（90s 无任何报文才关流）。"""
    await _teleop_send(req.device_id, {"type": "teleop", "keepalive": True})
    return {"ok": True}


@app.post("/api/teleop/center")
async def api_teleop_center(
    req: TeleopDeviceRequest, _: None = Depends(require_app_token)
) -> dict:
    await _teleop_send(req.device_id, {"type": "teleop", "center": True})
    return {"ok": True}


@app.post("/api/teleop/scan")
async def api_teleop_scan(req: TeleopDeviceRequest, _: None = Depends(require_app_token)) -> dict:
    await _teleop_send(req.device_id, {"type": "teleop", "scan": True})
    return {"ok": True}


@app.post("/api/teleop/photo")
async def api_teleop_photo(
    req: TeleopDeviceRequest, _: None = Depends(require_app_token)
) -> dict:
    await _teleop_send(req.device_id, {"type": "photo"})
    info = await video_registry.wait_photo(req.device_id, timeout=12.0)
    if info is None:
        raise HTTPException(status_code=504, detail="photo timeout")
    return info


@app.post("/api/teleop/ping")
async def api_teleop_ping(
    req: TeleopDeviceRequest, _: None = Depends(require_app_token)
) -> dict:
    """RTT 诊断：走视频通道（设备侧只做回声，不经过控制面）。"""
    vconn = video_registry.get(req.device_id)
    if vconn is None:
        raise HTTPException(status_code=409, detail="video channel offline")
    seq = int(time.time() * 1000) % 1_000_000
    ts = int(time.time() * 1000)
    try:
        await vconn.ws.send_json({"t": "ping", "seq": seq, "ts": ts})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"video send failed: {exc}") from exc
    rtt = await video_registry.wait_pong(req.device_id, seq, timeout=2.0)
    if rtt is None:
        raise HTTPException(status_code=504, detail="pong timeout")
    return {
        "rtt_ms": round(rtt["rtt_ms"], 1),
        "hold_us": rtt.get("hold_us"),
        "ps": rtt.get("ps"),
    }


@app.get("/api/devices/{device_id}/last_frame.jpg")
async def api_last_frame(
    device_id: str, _: None = Depends(require_app_token)
) -> Response:
    conn = video_registry.get(device_id)
    if conn is None or conn.last_frame is None:
        raise HTTPException(status_code=404, detail="no frame")
    return Response(
        content=conn.last_frame,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


@app.get("/api/devices/{device_id}/video_status")
async def api_video_status(
    device_id: str, _: None = Depends(require_app_token)
) -> dict:
    conn = video_registry.get(device_id)
    if conn is None:
        return {"online": False}
    age_ms = int((time.time() - conn.last_frame_ts) * 1000) if conn.last_frame_ts else None
    return {
        "online": True,
        "last_frame_age_ms": age_ms,
        "frames": conn.frames_received,
        "meta": conn.last_frame_meta,
        "memory": conn.memory,
    }


@app.get("/api/devices/{device_id}/photos")
async def api_photos(
    device_id: str, limit: int = 60, _: None = Depends(require_app_token)
) -> list[dict]:
    return list_photos(device_id, max(1, min(limit, 200)))


@app.delete("/api/devices/{device_id}/photos")
async def api_clear_photos(
    device_id: str, _: None = Depends(require_app_token)
) -> dict:
    """手动清空这台设备的相册（平时靠 save 时的 prune 保留最近 100 张 / 30 天）。"""
    return {"deleted": clear_photos(device_id)}


DOWNLOAD_URL_TTL_SECONDS = 3600


def _photo_download_sig(device_id: str, name: str, exp: int) -> str:
    msg = f"{device_id}|{name}|{exp}".encode()
    return hmac.new(settings.app_token.encode(), msg, hashlib.sha256).hexdigest()


def _photo_download_response(path: Path, name: str) -> FileResponse:
    # 文件名用拍摄时间：相册/下载列表里按时间排序更直观。
    # name 已由 find_photo 的 PHOTO_NAME_RE 校验为「毫秒 epoch.jpg」。
    try:
        shot_ts = int(name.split(".")[0]) / 1000
        filename = time.strftime("chan-%Y%m%d-%H%M%S.jpg", time.localtime(shot_ts))
    except (ValueError, OSError):
        filename = name
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
        filename=filename,
        content_disposition_type="attachment",
    )


@app.get("/api/devices/{device_id}/photos/{name}/download_url")
async def api_photo_download_url(
    device_id: str, name: str, _: None = Depends(require_app_token)
) -> dict:
    """换一个短时签名下载链接。

    WebView 的 <a download> 可能被当成普通导航，导航不会带 X-App-Token；
    签名 URL 让下载请求本身不依赖自定义头，WebView 导航或 DownloadManager 都能用。
    """
    if find_photo(device_id, name) is None:
        raise HTTPException(status_code=404, detail="not found")
    exp = int(time.time()) + DOWNLOAD_URL_TTL_SECONDS
    sig = _photo_download_sig(device_id, name, exp)
    return {"url": f"/api/devices/{device_id}/photos/{name}/download?exp={exp}&sig={sig}"}


@app.get("/api/devices/{device_id}/photos/{name}/download")
async def api_photo_download(
    device_id: str,
    name: str,
    request: Request,
    exp: int = 0,
    sig: str = "",
    x_app_token: str | None = Header(default=None, alias="X-App-Token"),
) -> FileResponse:
    path = find_photo(device_id, name)
    if path is None:
        raise HTTPException(status_code=404, detail="not found")
    header_ok = bool(x_app_token) and hmac.compare_digest(
        x_app_token.encode(), settings.app_token.encode()
    )
    sig_ok = bool(sig) and exp >= int(time.time()) and hmac.compare_digest(
        sig.encode(), _photo_download_sig(device_id, name, exp).encode()
    )
    logger.info(
        "photo download %s/%s ua=%r header=%s sig=%s",
        device_id,
        name,
        (request.headers.get("user-agent") or "")[:48],
        header_ok,
        sig_ok,
    )
    if not header_ok and not sig_ok:
        raise HTTPException(status_code=401, detail="invalid download token")
    return _photo_download_response(path, name)


@app.get("/api/devices/{device_id}/photos/{name}")
async def api_photo_file(
    device_id: str,
    name: str,
    download: bool = False,
    _: None = Depends(require_app_token),
) -> FileResponse:
    path = find_photo(device_id, name)
    if path is None:
        raise HTTPException(status_code=404, detail="not found")
    if not download:
        return FileResponse(
            path, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
        )
    return _photo_download_response(path, name)


@app.websocket("/device/video")
async def device_video(ws: WebSocket, token: str = "") -> None:
    if token != settings.device_token:
        await ws.close(code=4401)
        return

    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=settings.device_hello_timeout)
    except Exception:  # noqa: BLE001
        await ws.close(code=4400)
        return

    if hello.get("t") != "hello" or not hello.get("device_id"):
        await ws.close(code=4400)
        return

    device_id = str(hello["device_id"])
    conn = VideoConnection(device_id=device_id, ws=ws)
    video_registry.register(conn)
    logger.info("video channel connected: %s", device_id)

    try:
        while True:
            raw = await ws.receive()
            if raw.get("text") is not None:
                try:
                    msg = json.loads(raw["text"])
                except json.JSONDecodeError:
                    continue
                handle_video_text(conn, msg)
            elif raw.get("bytes") is not None:
                handle_video_binary(conn, raw["bytes"])
    except WebSocketDisconnect:
        logger.info("video channel disconnected: %s", device_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("video channel error (%s): %s", device_id, exc)
    finally:
        video_registry.unregister(device_id, conn)
