#!/usr/bin/env python3
"""番茄钟状态流转回归测试。

做法：起一段计时，然后**把 created_at 往前挪**来模拟时间流逝——比真等 10 分钟现实。
服务端所有判断都基于 created_at + duration（`_timer_view`），所以改这个字段等价于
"时间过去了这么久"，且不影响设备侧（设备有自己的单调计时，测试里用假设备）。

用法：
    .venv/bin/python selfhost/behavior/tools/check_timer_states.py
前置：chan-behavior 在跑，tools/fake_device.py 已连接（device id = fake-chan）。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8100"
TOKEN = "dev-token"
DEV = "fake-chan"
DB = Path(__file__).resolve().parent.parent / "data" / "chan.db"

passed = 0
failed = 0


def api(path: str, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        method=method,
        headers={"X-App-Token": TOKEN, "Content-Type": "application/json"},
        data=data,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read() or b"{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read() or b"{}"
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw.decode("utf-8", "replace")}


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}  {detail}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def shift(job_id: str, seconds: float) -> None:
    """把计时起点往前挪，等价于"已经过去 seconds 秒"。

    末尾那 0.35 秒是必须的：服务端在容器里，DB 是 macOS 的 bind mount，
    Docker Desktop 的文件缓存有几百毫秒延迟——改完立刻查，服务端读到的还是旧值
    （实测 0ms 读到 focus，300ms 后读到 overtime）。这是环境特性，不是服务端缓存。
    """
    conn = sqlite3.connect(DB)
    try:
        conn.execute(
            "UPDATE jobs SET created_at = created_at - ? WHERE id = ?", (seconds, job_id)
        )
        conn.commit()
    finally:
        conn.close()
    time.sleep(0.35)


def start(duration_ms: int = 60_000, task: str = "状态测试") -> str:
    status, body = api(
        "/api/timers",
        "POST",
        {"duration_ms": duration_ms, "label": "专注", "task": task, "device_id": DEV},
    )
    if status != 200:
        raise RuntimeError(f"start failed: {status} {body}")
    return body["timer"]["job_id"]


def clear_active() -> None:
    """把残留的进行中计时清掉（上一轮测试中断会留下）。"""
    for _ in range(3):
        _, body = api(f"/api/timers/active?device_id={DEV}")
        timer = body.get("timer")
        if not timer:
            return
        api(f"/api/jobs/{timer['job_id']}/cancel", "POST")
        time.sleep(0.2)


def main() -> int:
    status, _ = api("/api/devices")
    if status != 200:
        print("服务端不可用")
        return 1
    _, devices = api("/api/devices")
    if not any(d["id"] == DEV and d["online"] for d in devices):
        print(f"假设备 {DEV} 不在线，先跑 tools/fake_device.py")
        return 1

    clear_active()
    print("\n[1] 提前完成 → early")
    job = start()
    status, body = api(f"/api/jobs/{job}/finish", "POST")
    check("finish 返回 200", status == 200, f"status={status}")
    check("结局 early", body.get("outcome") == "early", f"outcome={body.get('outcome')}")
    check("状态 done", body.get("status") == "done", f"status={body.get('status')}")

    print("\n[2] 到点 → overtime（不再是 done）")
    job = start(60_000)
    shift(job, 70)
    status, body = api(f"/api/timers/active?device_id={DEV}")
    timer = body.get("timer")
    check("active 仍返回计时", timer is not None)
    check("phase=overtime", timer and timer.get("phase") == "overtime",
          f"phase={timer.get('phase') if timer else None}")
    _, job_body = api(f"/api/jobs/{job}")
    check("库状态 overtime", job_body.get("status") == "overtime",
          f"status={job_body.get('status')}")

    print("\n[3] 超时 10 秒内 finish → on_time")
    status, body = api(f"/api/jobs/{job}/finish", "POST")
    check("结局 on_time", body.get("outcome") == "on_time", f"outcome={body.get('outcome')}")

    print("\n[4] 超时 140 秒 finish → late")
    job = start(60_000)
    shift(job, 200)
    api(f"/api/timers/active?device_id={DEV}")
    status, body = api(f"/api/jobs/{job}/finish", "POST")
    check("结局 late", body.get("outcome") == "late", f"outcome={body.get('outcome')}")

    print("\n[5] 超时 10 分钟 → expired（惰性推进）")
    job = start(60_000)
    shift(job, 700)
    status, body = api(f"/api/timers/active?device_id={DEV}")
    check("active 返回 null", body.get("timer") is None, f"body={body}")
    _, job_body = api(f"/api/jobs/{job}")
    check("库状态 expired", job_body.get("status") == "expired",
          f"status={job_body.get('status')}")
    check("结局 expired", job_body.get("outcome") == "expired",
          f"outcome={job_body.get('outcome')}")

    print("\n[6] 取消 → canceled")
    job = start(60_000)
    status, body = api(f"/api/jobs/{job}/cancel", "POST")
    check("状态 canceled", body.get("status") == "canceled", f"status={body.get('status')}")
    check("没有 outcome", body.get("outcome") in (None, ""), f"outcome={body.get('outcome')}")

    print("\n[7] 超时期间不许起新计时（409）")
    job = start(60_000)
    shift(job, 70)
    api(f"/api/timers/active?device_id={DEV}")
    status, body = api(
        "/api/timers", "POST", {"duration_ms": 60_000, "task": "第二个", "device_id": DEV}
    )
    check("返回 409", status == 409, f"status={status} {body}")
    api(f"/api/jobs/{job}/finish", "POST")

    print("\n[8] 重复 finish 幂等，不改结局")
    job = start(60_000)
    api(f"/api/jobs/{job}/finish", "POST")
    status, body = api(f"/api/jobs/{job}/finish", "POST")
    check("第二次仍 200", status == 200, f"status={status}")
    check("结局仍是 early", body.get("outcome") == "early", f"outcome={body.get('outcome')}")

    print("\n[9] 播报任务名截断到 12 字")
    job = start(60_000, task="这是一个特别特别长的任务名称超过了十二个字")
    shift(job, 70)
    api(f"/api/timers/active?device_id={DEV}")
    _, job_body = api(f"/api/jobs/{job}")
    check("任务名原样存在库里", len(job_body.get("payload", {}).get("task", "")) > 12,
          f"len={len(job_body.get('payload', {}).get('task', ''))}")
    api(f"/api/jobs/{job}/finish", "POST")

    clear_active()
    print(f"\n结果：{passed} 通过，{failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
