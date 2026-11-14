#!/usr/bin/env python3
"""MiMo vs EdgeTTS 离线 A/B：同一句话各合一遍，落成文件供试听，并打印耗时/时长。

不需要设备，也不需要重启容器。用来快速选音色/调 style 标签；
真机验收还是要在设备上说一句（见 selfhost/README.md）。

用法：
    ./ab.py "真的假的啦，你今天居然记得吃饭"
    ./ab.py "哎呀妈呀，这天儿也忒冷了吧" --style "(东北话)" --voice 苏打
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _load_env() -> None:
    """读 .env（与 run.sh 一致），必须在 import server 之前。"""
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_env()
import server  # noqa: E402

CONTAINER = "xiaozhi-esp32-server"
# 与服务端配置一致的默认音色
EDGE_VOICE = "zh-CN-XiaoxiaoNeural"

EDGE_SNIPPET = """
import asyncio, edge_tts, sys
text, voice, out = sys.argv[1], sys.argv[2], sys.argv[3]
async def main():
    c = edge_tts.Communicate(text, voice=voice)
    with open(out, "wb") as f:
        async for chunk in c.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
asyncio.run(main())
"""


def wav_duration(path: str) -> float | None:
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / w.getframerate()
    except Exception:  # noqa: BLE001
        return None


def afinfo_duration(path: str) -> float | None:
    if not shutil.which("afinfo"):
        return None
    try:
        out = subprocess.run(
            ["afinfo", path], capture_output=True, text=True, timeout=10
        ).stdout
        for line in out.splitlines():
            if "estimated duration" in line:
                return float(line.split(":")[1].strip().split()[0])
    except Exception:  # noqa: BLE001
        pass
    return None


def edge_sample(text: str, out_path: str) -> float:
    """用容器里的 edge_tts 合成（和线上 EdgeTTS provider 同一份库/音色）。"""
    started = time.monotonic()
    script = f"import sys; {EDGE_SNIPPET}"
    subprocess.run(
        ["docker", "exec", CONTAINER, "python", "-c", script, text, EDGE_VOICE,
         "/tmp/ab_edge.mp3"],
        check=True,
        capture_output=True,
        timeout=120,
    )
    subprocess.run(
        ["docker", "cp", f"{CONTAINER}:/tmp/ab_edge.mp3", out_path],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return (time.monotonic() - started) * 1000


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("text")
    p.add_argument("--voice", default=os.environ.get("MIMO_VOICE", "冰糖"))
    p.add_argument("--style", default="", help="音频标签，拼在待合成文本开头，如 (台湾腔)")
    p.add_argument("--instruction", default="", help="自然语言风格指令，放 user 轮")
    p.add_argument("--outdir", default=os.path.join(HERE, "out"))
    args = p.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    stamp = time.strftime("%H%M%S")
    edge_path = os.path.join(args.outdir, f"{stamp}-edge.mp3")
    mimo_path = os.path.join(args.outdir, f"{stamp}-mimo.wav")

    print(f"文本：{args.text}")
    print(f"EdgeTTS  {EDGE_VOICE}")

    edge_ms = edge_sample(args.text, edge_path)
    edge_s = afinfo_duration(edge_path)
    print(f"  耗时 {edge_ms:.0f}ms  大小 {os.path.getsize(edge_path)}B"
          + (f"  时长 {edge_s:.2f}s" if edge_s else ""))

    label = f"MiMo  {args.voice}" + (f"  style={args.style}" if args.style else "")
    print(label)
    started = time.monotonic()
    try:
        audio = server.synth(
            args.text, voice=args.voice, style=args.style,
            instruction=args.instruction,
        )
    except Exception as e:  # noqa: BLE001
        print(f"  失败：{e}")
        return 1
    mimo_ms = (time.monotonic() - started) * 1000
    with open(mimo_path, "wb") as f:
        f.write(audio)
    mimo_s = wav_duration(mimo_path)
    print(f"  耗时 {mimo_ms:.0f}ms  大小 {len(audio)}B"
          + (f"  时长 {mimo_s:.2f}s" if mimo_s else ""))

    print("\n试听：")
    print(f"  open {edge_path}")
    print(f"  open {mimo_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
