#!/usr/bin/env python3
"""假设备：不刷固件也能验证「App → 服务端 → 控制 WS → ack」全链路。

用法（容器内）：
    docker compose exec chan-behavior python tools/fake_device.py
或本机：
    FAKE_DEVICE_URL=ws://127.0.0.1:8100/device/ws?token=dev-device-token python tools/fake_device.py
"""

import asyncio
import json
import os

import websockets

URL = os.environ.get(
    "FAKE_DEVICE_URL", "ws://127.0.0.1:8100/device/ws?token=dev-device-token"
)


async def heartbeat(ws) -> None:
    while True:
        await asyncio.sleep(10)
        await ws.send(json.dumps({"type": "heartbeat"}))


async def session() -> None:
    async with websockets.connect(URL, ping_interval=20) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "hello",
                    "device_id": "fake-chan",
                    "name": "Fake Chan",
                    "fw": "p0-test",
                    "state": {"voice": "listening"},
                }
            )
        )
        print("welcome:", await ws.recv(), flush=True)
        hb = asyncio.create_task(heartbeat(ws))
        audio_bytes = 0
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    # 音频分块：真机按 audio_begin 的 len 收满再播，这里只统计
                    audio_bytes += len(raw)
                    continue
                msg = json.loads(raw)
                print("recv:", json.dumps(msg, ensure_ascii=False)[:500], flush=True)
                if msg.get("type") == "audio_end":
                    print(f"audio {msg.get('audio_id')}: {audio_bytes} bytes", flush=True)
                    audio_bytes = 0
                if msg.get("type") == "behavior":
                    script = msg["script"]
                    job_id = script.get("job_id")
                    await ws.send(json.dumps({"type": "job_started", "job_id": job_id}))
                    print(f"job {job_id}: started (simulated)", flush=True)
                    await asyncio.sleep(2.0)
                    await ws.send(json.dumps({"type": "job_done", "job_id": job_id}))
                    print(f"job {job_id}: done (simulated)", flush=True)
        finally:
            hb.cancel()


async def main() -> None:
    """断线自动重连：服务端 --reload 每次改代码都会把连接踢掉（1012）。"""
    while True:
        try:
            await session()
            print("connection closed, reconnecting…", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"disconnected: {type(exc).__name__}: {exc}", flush=True)
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
