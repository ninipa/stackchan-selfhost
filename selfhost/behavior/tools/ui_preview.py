#!/usr/bin/env python3
"""本地 UI 预览服务：只给 selfhost/behavior/app/static/index.html 喂假数据。

用途：没有真机、或者不想动 mini 上的生产数据时，看新界面长什么样。
它不是服务端实现，不连数据库、不连设备、不合成音频；所有 POST 都只在内存里改。

    python3 tools/ui_preview.py            # http://127.0.0.1:8111/app
    python3 tools/ui_preview.py --port 8111

状态由页面 URL 的 `demo` 参数控制（通过 Referer 传给接口）：
    /app?demo=running     番茄钟进行中
    /app?demo=fuse        进入收尾（最后 20%）
    /app?demo=overtime    已超时
    /app?demo=offline     设备离线
"""

from __future__ import annotations

import argparse
import json
import time
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "app" / "static" / "index.html"

NOW = time.time()
ACTIVE_TIMER: dict | None = None
JOBS: list[dict] = []


def _job(
    job_id: str,
    job_type: str,
    payload: dict,
    status: str,
    created_at: float,
    *,
    outcome: str | None = None,
    error: str | None = None,
    finished_at: float | None = None,
) -> dict:
    return {
        "id": job_id,
        "device_id": "demo-device",
        "type": job_type,
        "payload": payload,
        "script": None,
        "status": status,
        "outcome": outcome,
        "error": error,
        "created_at": created_at,
        "sent_at": created_at + 1 if status != "pending" else None,
        "finished_at": finished_at,
    }


def _seed_jobs() -> None:
    today = time.localtime()
    midnight = time.mktime(
        (today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, -1)
    )
    JOBS.extend(
        [
            _job("msg-8c21a0", "message", {"text": "该喝水啦", "repeat": 1},
                 "done", NOW - 12 * 60, finished_at=NOW - 11 * 60),
            _job("tmr-7d4f2a", "timer", {"duration_ms": 25 * 60000, "label": "专注", "task": "写周报"},
                 "done", NOW - 3 * 3600, outcome="early", finished_at=NOW - 2.8 * 3600),
            _job("msg-2b91cc", "message", {"text": "我回来啦", "repeat": 2},
                 "done", NOW - 5 * 3600, finished_at=NOW - 5 * 3600 + 40),
            _job("tmr-51aa03", "timer", {"duration_ms": 45 * 60000, "label": "专注", "task": "整理照片"},
                 "done", NOW - 6 * 3600, outcome="on_time", finished_at=NOW - 5.1 * 3600),
            _job("msg-9d02ef", "message", {"text": "吃饭啦", "repeat": 1},
                 "error", NOW - 8 * 3600, error="device offline", finished_at=NOW - 8 * 3600),
            _job("tmr-3e8b71", "timer", {"duration_ms": 15 * 60000, "label": "专注", "task": "回邮件"},
                 "canceled", NOW - 26 * 3600, finished_at=NOW - 25.7 * 3600),
            _job("msg-77c1b4", "message", {"text": "晚安", "repeat": 1},
                 "done", NOW - 28 * 3600, finished_at=NOW - 28 * 3600 + 20),
            _job("tmr-0c9d12", "timer", {"duration_ms": 60 * 60000, "label": "专注", "task": "读书"},
                 "done", NOW - 30 * 3600, outcome="late", finished_at=NOW - 29 * 3600),
            _job("msg-4f60aa", "message", {"text": "该睡觉啦", "repeat": 1},
                 "done", midnight - 40 * 60, finished_at=midnight - 39 * 60),
        ]
    )


_seed_jobs()


def _demo(referer: str | None) -> str:
    if not referer:
        return ""
    try:
        return parse_qs(urlparse(referer).query).get("demo", [""])[0]
    except Exception:  # noqa: BLE001
        return ""


def _device(demo: str) -> dict:
    offline = demo == "offline"
    return {
        "id": "demo-device",
        "name": "StackChan",
        "fw": "1.6.2",
        "last_seen": time.time() - (600 if offline else 8),
        "state": {},
        "online": not offline,
    }


def _active_timer(demo: str) -> dict | None:
    global ACTIVE_TIMER
    if ACTIVE_TIMER is not None:
        return ACTIVE_TIMER
    if demo not in ("running", "fuse", "overtime"):
        return None
    duration = 25 * 60000
    if demo == "running":
        elapsed = 7 * 60000
    elif demo == "fuse":
        elapsed = 21 * 60000
    else:
        elapsed = 27 * 60000
    remaining = max(0, duration - elapsed)
    overtime = max(0, elapsed - duration)
    phase = "overtime" if overtime else ("fuse" if remaining <= duration * 0.2 else "focus")
    return {
        "type": "timer_start",
        "job_id": "tmr-demo01",
        "phase": phase,
        "duration_ms": duration,
        "remaining_ms": remaining,
        "overtime_ms": overtime,
        "label": "专注",
        "task": "写周报",
    }


def _stats() -> dict:
    return {
        "jobs": len(JOBS) + (1 if ACTIVE_TIMER else 0),
        "db_bytes": 184320,
        "audio_files": 12,
        "audio_bytes": 1284 * 1024,
        "history_keep": 200,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ChanUIPreview/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        print("%s - %s" % (self.address_string(), fmt % args))

    # ---------- helpers ----------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    def _demo(self) -> str:
        return _demo(self.headers.get("Referer"))

    # ---------- routes ----------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/", "/app", "/index.html"):
            html = INDEX.read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
            return
        if path == "/frame":
            # macOS 的 headless Chrome 窗口最小约 500 宽，直接开 390 会裁掉右侧。
            # 用一个 390 宽的 iframe 还原手机视口，截图后再按 iframe 区域裁切。
            w = int(qs.get("w", ["390"])[0])
            h = int(qs.get("h", ["844"])[0])
            src = qs.get("src", ["/app"])[0]
            # 只允许预览自己的页面，避免这台机器上的其它人把它当开放 iframe 用
            if not src.startswith("/app"):
                src = "/app"
            src = escape(src, quote=True)
            html = (
                '<!DOCTYPE html><html><head><meta charset="utf-8">'
                '<style>html,body{margin:0;background:#D8D1C4}'
                f'iframe{{display:block;width:{w}px;height:{h}px;border:0;background:#F5F0E7}}'
                '</style></head><body>'
                f'<iframe src="{src}"></iframe></body></html>'
            ).encode()
            self._send(200, html, "text/html; charset=utf-8")
            return
        if path == "/health":
            self._json({"ok": True, "devices_online": 0 if self._demo() == "offline" else 1})
            return
        if path == "/api/devices":
            if self._demo() == "nodevice":
                self._json([])
            else:
                self._json([_device(self._demo())])
            return
        if path == "/api/jobs":
            limit = int(qs.get("limit", ["20"])[0])
            active = _active_timer(self._demo())
            rows = sorted(JOBS, key=lambda j: j["created_at"], reverse=True)
            if active:
                rows.insert(0, _job(
                    active["job_id"], "timer",
                    {"duration_ms": active["duration_ms"], "label": active["label"], "task": active["task"]},
                    "running", time.time() - 7 * 60,
                ))
            self._json(rows[:limit])
            return
        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1].split("?")[0]
            for job in JOBS:
                if job["id"] == job_id:
                    # 消息发出 2 秒后自动完成，方便看状态变化
                    if job["type"] == "message" and job["status"] == "sent" and time.time() - job["created_at"] > 2:
                        job["status"] = "done"
                        job["finished_at"] = time.time()
                    self._json(job)
                    return
            active = _active_timer(self._demo())
            if active and active["job_id"] == job_id:
                self._json(_job(job_id, "timer", {"duration_ms": active["duration_ms"],
                                                  "label": active["label"], "task": active["task"]},
                                "running", time.time() - 7 * 60))
                return
            self._json({"detail": "job not found"}, 404)
            return
        if path == "/api/timers/active":
            self._json({"timer": _active_timer(self._demo())})
            return
        if path == "/api/settings":
            self._json({
                "values": {"history_keep": 200},
                "spec": {"history_keep": {"type": "int", "min": 20, "max": 5000, "label": "历史保留条数"}},
                "readonly": {
                    "xiaozhi_voice": "zh-CN-XiaoyiNeural",
                    "behavior_voice": "zh-CN-XiaoyiNeural",
                },
            })
            return
        if path == "/api/maintenance/stats":
            self._json(_stats())
            return
        self._json({"detail": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        global ACTIVE_TIMER
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json()

        if path == "/api/messages":
            job = _job(
                f"msg-{int(time.time() * 1000) % 0xFFFFFF:06x}",
                "message",
                {"text": body.get("text", ""), "repeat": body.get("repeat", 1)},
                "sent",
                time.time(),
            )
            JOBS.insert(0, job)
            self._json(job)
            return
        if path == "/api/timers":
            duration = int(body.get("duration_ms", 25 * 60000))
            ACTIVE_TIMER = {
                "type": "timer_start",
                "job_id": "tmr-" + str(int(time.time()))[-6:],
                "phase": "focus",
                "duration_ms": duration,
                "remaining_ms": duration,
                "overtime_ms": 0,
                "label": body.get("label", "专注"),
                "task": body.get("task", ""),
            }
            self._json({"job": None, "timer": ACTIVE_TIMER})
            return
        if path.startswith("/api/jobs/") and path.endswith("/finish"):
            ACTIVE_TIMER = None
            self._json({"status": "done", "outcome": "early"})
            return
        if path.startswith("/api/jobs/") and path.endswith("/cancel"):
            ACTIVE_TIMER = None
            self._json({"status": "canceled"})
            return
        if path == "/api/settings":
            self._json({"values": {"history_keep": body.get("history_keep", 200)}})
            return
        if path == "/api/maintenance/cleanup":
            self._json({"jobs_deleted": 3 if body.get("history") else 0,
                        "audio_deleted": 2 if body.get("audio") else 0})
            return
        self._json({"detail": "not found"}, 404)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8111)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"UI preview: http://{args.host}:{args.port}/app")
    print("demo: ?demo=running | fuse | overtime | offline")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
