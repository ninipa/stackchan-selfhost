"""遥控台服务端支撑：视频通道、拍照存储、会话辅助。

设备侧固件（`main/hal/behavior/teleop.cpp`）通过 `/device/video` 接入：
  设备 -> 服务端：{"t":"hello","device_id":...}
                   {"t":"frame","seq","len","w","h","enc_ms","int_largest","dma_largest"} + 二进制块
                   {"t":"photo","len","w","h","enc_ms"} + 二进制块 + {"t":"photo_end"}
  服务端不主动下发视频命令；流的启停由控制面 `teleop` 消息驱动（begin/end）。

App 侧走轮询：`GET /api/devices/{id}/last_frame.jpg`，不需要 App 视频 WS。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import WebSocket

from .config import settings

logger = logging.getLogger("chan-behavior.teleop")

PHOTO_RETENTION = 100          # 每台设备最多保留的张数
PHOTO_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 天
PHOTO_NAME_RE = re.compile(r"^\d{10,16}\.jpg$")


@dataclass
class VideoConnection:
    device_id: str
    ws: WebSocket
    connected_at: float = field(default_factory=time.time)
    last_frame: bytes | None = None
    last_frame_meta: dict[str, Any] = field(default_factory=dict)
    last_frame_ts: float = 0.0
    frames_received: int = 0
    # 当前正在攒的消息
    pending_kind: str | None = None
    pending_meta: dict[str, Any] = field(default_factory=dict)
    pending_buf: bytearray | None = None
    # 设备上报的内存（诊断用）
    memory: dict[str, Any] = field(default_factory=dict)


class VideoRegistry:
    def __init__(self) -> None:
        self._conns: dict[str, VideoConnection] = {}
        self._photo_waiters: dict[str, list[asyncio.Future]] = {}
        self._pong_waiters: dict[tuple[str, int], asyncio.Future] = {}

    def register(self, conn: VideoConnection) -> None:
        self._conns[conn.device_id] = conn

    def unregister(self, device_id: str, conn: VideoConnection | None = None) -> None:
        if conn is None or self._conns.get(device_id) is conn:
            self._conns.pop(device_id, None)

    def get(self, device_id: str | None) -> VideoConnection | None:
        return self._conns.get(device_id) if device_id else None

    def list(self) -> list[VideoConnection]:
        return list(self._conns.values())

    # ---------- 拍照完成通知 ----------

    def notify_photo(self, device_id: str, info: dict[str, Any]) -> None:
        for fut in self._photo_waiters.pop(device_id, []):
            if not fut.done():
                fut.set_result(info)

    async def wait_photo(self, device_id: str, timeout: float = 12.0) -> dict[str, Any] | None:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._photo_waiters.setdefault(device_id, []).append(fut)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            waiters = self._photo_waiters.get(device_id)
            if waiters and fut in waiters:
                waiters.remove(fut)

    # ---------- ping/pong（RTT 诊断） ----------

    def notify_pong(self, device_id: str, seq: int, result: dict[str, Any]) -> None:
        fut = self._pong_waiters.pop((device_id, seq), None)
        if fut is not None and not fut.done():
            fut.set_result(result)

    async def wait_pong(
        self, device_id: str, seq: int, timeout: float = 2.0
    ) -> dict[str, Any] | None:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pong_waiters[(device_id, seq)] = fut
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            if self._pong_waiters.get((device_id, seq)) is fut:
                self._pong_waiters.pop((device_id, seq), None)


video_registry = VideoRegistry()


# ---------- 视频通道消息处理 ----------


def handle_video_text(conn: VideoConnection, msg: dict[str, Any]) -> None:
    """处理设备视频通道上的文本消息（JSON 头/结束标记/统计）。"""
    kind = msg.get("t")
    if kind in ("frame", "photo"):
        conn.pending_kind = str(kind)
        conn.pending_meta = msg
        conn.pending_buf = bytearray()
        return
    if kind == "photo_end":
        _finish_pending(conn, complete=True)
        return
    if kind == "pong":
        ts = msg.get("ts")
        seq = msg.get("seq")
        if isinstance(ts, (int, float)) and isinstance(seq, int):
            video_registry.notify_pong(
                conn.device_id,
                seq,
                {
                    "rtt_ms": time.time() * 1000 - ts,
                    "hold_us": msg.get("hold_us"),
                    "ps": msg.get("ps"),
                },
            )
        return
    if kind == "stat":
        conn.memory = {k: v for k, v in msg.items() if k != "t"}
        return
    logger.debug("video %s: unhandled text %s", conn.device_id, kind)


def handle_video_binary(conn: VideoConnection, data: bytes) -> None:
    """累积二进制块；帧攒满即存为最新帧，照片攒满等 photo_end 再落盘。"""
    if conn.pending_buf is None:
        return
    conn.pending_buf += data
    need = int(conn.pending_meta.get("len") or 0)
    if need <= 0 or len(conn.pending_buf) < need:
        return
    if conn.pending_kind == "frame":
        raw = bytes(conn.pending_buf[:need])
        meta = conn.pending_meta
        conn.last_frame = raw
        conn.last_frame_meta = {
            "w": meta.get("w"),
            "h": meta.get("h"),
            "kb": round(need / 1024, 1),
            "enc_ms": meta.get("enc_ms"),
            "seq": meta.get("seq"),
            "int_largest": meta.get("int_largest"),
            "dma_largest": meta.get("dma_largest"),
        }
        conn.last_frame_ts = time.time()
        conn.frames_received += 1
        conn.pending_kind = None
        conn.pending_buf = None
        conn.pending_meta = {}
    elif conn.pending_kind == "photo" and len(conn.pending_buf) >= need:
        # 照片收满后等 photo_end（设备紧接着发）再落盘，避免半张图
        _finish_pending(conn, complete=True)


def _finish_pending(conn: VideoConnection, complete: bool) -> None:
    if conn.pending_kind == "photo" and conn.pending_buf is not None:
        need = int(conn.pending_meta.get("len") or 0)
        if complete and need > 0 and len(conn.pending_buf) >= need:
            info = save_photo(conn.device_id, bytes(conn.pending_buf[:need]), conn.pending_meta)
            if info:
                video_registry.notify_photo(conn.device_id, info)
        elif not complete:
            logger.warning("photo from %s incomplete: %d/%d", conn.device_id, len(conn.pending_buf), need)
    conn.pending_kind = None
    conn.pending_buf = None
    conn.pending_meta = {}


# ---------- 照片存储 ----------


def _safe_device_dir(device_id: str) -> Path:
    safe = "".join(c for c in device_id if c.isalnum() or c in "-_")
    return Path(settings.data_dir) / "photos" / (safe or "unknown")


def save_photo(device_id: str, raw: bytes, meta: dict[str, Any]) -> dict[str, Any] | None:
    out_dir = _safe_device_dir(device_id)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"{int(time.time() * 1000)}.jpg"
        (out_dir / name).write_bytes(raw)
        prune_photos(device_id)
        logger.info("photo saved: %s/%s (%.1fKB)", device_id, name, len(raw) / 1024)
        return {
            "name": name,
            "w": meta.get("w"),
            "h": meta.get("h"),
            "kb": round(len(raw) / 1024, 1),
            "ts": time.time(),
            "url": f"/api/devices/{device_id}/photos/{name}",
        }
    except OSError as exc:  # noqa: BLE001
        logger.warning("photo save failed for %s: %s", device_id, exc)
        return None


def prune_photos(device_id: str) -> None:
    out_dir = _safe_device_dir(device_id)
    try:
        files = sorted(out_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    now = time.time()
    for idx, path in enumerate(files):
        try:
            too_old = now - path.stat().st_mtime > PHOTO_MAX_AGE_SECONDS
            if idx >= PHOTO_RETENTION or too_old:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def list_photos(device_id: str, limit: int = 60) -> list[dict[str, Any]]:
    out_dir = _safe_device_dir(device_id)
    if not out_dir.exists():
        return []
    try:
        files = sorted(out_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    except OSError:
        return []
    return [
        {
            "name": p.name,
            "ts": p.stat().st_mtime,
            "url": f"/api/devices/{device_id}/photos/{p.name}",
        }
        for p in files
    ]


def find_photo(device_id: str, name: str) -> Path | None:
    if not PHOTO_NAME_RE.fullmatch(name):
        return None
    path = _safe_device_dir(device_id) / name
    return path if path.exists() else None


def clear_photos(device_id: str) -> int:
    """删除这台设备的全部照片，返回删除张数（手动清理入口）。"""
    out_dir = _safe_device_dir(device_id)
    if not out_dir.exists():
        return 0
    deleted = 0
    for path in out_dir.glob("*.jpg"):
        if not PHOTO_NAME_RE.fullmatch(path.name):
            continue
        try:
            path.unlink()
            deleted += 1
        except OSError:
            continue
    return deleted
