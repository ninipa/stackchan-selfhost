#!/usr/bin/env python3
"""用文字模拟一次完整对话，验证服务端 TTS/Opus 输出。

跑在 xiaozhi-esp32-server 容器里：
    docker cp tools/test_xiaozhi_text.py xiaozhi-esp32-server:/tmp/
    docker exec xiaozhi-esp32-server python /tmp/test_xiaozhi_text.py "你好"

做三件事：
1. 按 xiaozhi WS 协议发 hello + listen(detect, text)，让服务端跑完整 LLM+TTS；
2. 记录每个文本消息、每个 Opus 包的到达时间（首响、最大间隔、总时长）；
3. 把 Opus 包按 [uint16 len][payload] 存盘，可直接用 decode_opus_dump.py 解码。
"""

import asyncio
import json
import struct
import sys
import time

import websockets

URL = "ws://127.0.0.1:8000/xiaozhi/v1/"


async def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else "请介绍一下杭州西湖，大概一百五十个字。"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "/tmp/response_opus.bin"
    timeout = float(sys.argv[3]) if len(sys.argv) > 3 else 60.0

    t0 = time.time()
    packets = []
    arrivals = []
    sentence_starts = []
    first_audio = None
    stop_at = None
    last_text_at = None

    async with websockets.connect(URL, additional_headers={"device-id": "test-text-client"}) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "hello",
                    "version": 1,
                    "transport": "websocket",
                    "audio_params": {
                        "format": "opus",
                        "sample_rate": 16000,
                        "channels": 1,
                        "frame_duration": 60,
                    },
                    "features": {"mcp": False},
                }
            )
        )

        # 等 welcome
        deadline = time.time() + 10
        while time.time() < deadline:
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            if isinstance(msg, str):
                data = json.loads(msg)
                print(f"{time.time() - t0:6.2f}s TEXT {data}")
                if data.get("type") == "hello":
                    break

        print(f"{time.time() - t0:6.2f}s >>> listen.detect text={text!r}")
        await ws.send(
            json.dumps({"type": "listen", "state": "detect", "text": text, "mode": "manual"})
        )

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=25)
            except asyncio.TimeoutError:
                print(f"{time.time() - t0:6.2f}s (25s no message, end)")
                break
            now = time.time() - t0
            if isinstance(msg, str):
                data = json.loads(msg)
                last_text_at = now
                print(f"{now:6.2f}s TEXT {data}")
                if data.get("type") == "tts" and data.get("state") == "sentence_start":
                    sentence_starts.append((now, data.get("text", "")))
                if data.get("type") == "tts" and data.get("state") == "stop":
                    stop_at = now
                    break
            else:
                arrivals.append(now)
                packets.append(msg)
                if first_audio is None:
                    first_audio = now
                    print(f"{now:6.2f}s <<< first audio packet ({len(msg)} bytes)")

    with open(out_path, "wb") as f:
        for packet in packets:
            f.write(struct.pack("<H", len(packet)))
            f.write(packet)

    print()
    print(f"Opus packets: {len(packets)} -> {out_path}")
    print(f"audio duration: {len(packets) * 0.06:.2f}s")
    print(f"sentence_start count: {len(sentence_starts)}")
    for at, sentence in sentence_starts:
        print(f"  {at:6.2f}s {sentence[:60]!r}{'...' if len(sentence) > 60 else ''}")
    print(f"first audio: {first_audio if first_audio is not None else 'N/A'}")
    print(f"stop: {stop_at}")
    if arrivals:
        gaps = [b - a for a, b in zip(arrivals, arrivals[1:])]
        print(
            f"arrival gaps: max={max(gaps) * 1000:.0f}ms "
            f"p95={sorted(gaps)[int(len(gaps) * 0.95)] * 1000:.0f}ms "
            f"mean={sum(gaps) / len(gaps) * 1000:.0f}ms"
        )
        print(f"audio span: {arrivals[-1] - arrivals[0]:.2f}s")
    if first_audio is not None and stop_at is not None:
        print(f"first_audio -> stop: {stop_at - first_audio:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
