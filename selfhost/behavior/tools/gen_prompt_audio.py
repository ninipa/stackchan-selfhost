#!/usr/bin/env python3
"""生成固定台词的音频（预生成，避免播出时才合成）。

两种产物：

  1. 行为服务缓存（默认）——写进 /data/audio/<sha1(音色|文本)>.ogg，
     也就是 tts.synth() 的缓存目录。生成一次后，服务端播这句永远是缓存命中，
     零延迟、零 token。
  2. WAV（可选 --wav）——24kHz/16bit/单声道，用于 xiaozhi 侧的固定提示音
     （如 end_prompt.wav）。生成后要放到 selfhost/assets/ 并在 compose 里挂载。

必须在容器里跑（容器有 ffmpeg 和 edge_tts）：

    docker exec chan-behavior python /app/tools/gen_prompt_audio.py "要抓紧了哟"
    docker exec chan-behavior python /app/tools/gen_prompt_audio.py \\
        "我先去待机啦，下次聊" --wav /data/prompts/end_prompt.wav

然后从宿主机取 WAV：

    cp selfhost/behavior/data/prompts/end_prompt.wav selfhost/assets/end_prompt.wav
"""

import argparse
import asyncio
import hashlib
import os
import subprocess
import sys
import tempfile

import edge_tts

VOICE = os.environ.get("TTS_VOICE", "zh-CN-XiaoyiNeural")
DATA_DIR = os.environ.get("DATA_DIR", "/data")


def cache_key(text: str, voice: str) -> str:
    return hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()[:16]


async def synth_mp3(text: str, voice: str) -> str:
    tmp = os.path.join(tempfile.gettempdir(), f"prompt-{cache_key(text, voice)}.mp3")
    await edge_tts.Communicate(text, voice=voice).save(tmp)
    return tmp


def to_ogg(mp3: str, out: str) -> None:
    """行为服务播放用的格式：16k 单声道 opus 32kbps。"""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", mp3,
         "-ar", "16000", "-ac", "1", "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", out],
        check=True,
    )


def to_wav(mp3: str, out: str) -> None:
    """xiaozhi 侧提示音格式：24k 单声道 16bit（与现有 end_prompt.wav 一致）。"""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", mp3,
         "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", out],
        check=True,
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", help="要生成的台词")
    ap.add_argument("--voice", default=VOICE, help=f"音色，默认 {VOICE}")
    ap.add_argument("--wav", help="额外输出一个 WAV 到指定路径（容器内路径）")
    ap.add_argument("--force", action="store_true", help="缓存已存在也重新合成")
    args = ap.parse_args()

    key = cache_key(args.text, args.voice)
    ogg_path = os.path.join(DATA_DIR, "audio", f"{key}.ogg")

    if os.path.exists(ogg_path) and not args.force and not args.wav:
        print(f"已存在，跳过：{ogg_path}")
        mp3 = None
    else:
        mp3 = await synth_mp3(args.text, args.voice)

    if mp3 and (args.force or not os.path.exists(ogg_path)):
        os.makedirs(os.path.dirname(ogg_path), exist_ok=True)
        to_ogg(mp3, ogg_path)
        size = os.path.getsize(ogg_path)
        print(f"缓存已生成：{ogg_path}（{size} 字节）")

    if args.wav and mp3:
        os.makedirs(os.path.dirname(args.wav) or ".", exist_ok=True)
        to_wav(mp3, args.wav)
        print(f"WAV 已生成：{args.wav}（{os.path.getsize(args.wav)} 字节）")

    if mp3 and os.path.exists(mp3):
        os.unlink(mp3)

    print(f"音色：{args.voice}  缓存 key：{key}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
