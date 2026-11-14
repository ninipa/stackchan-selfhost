import asyncio
import hashlib
import os
import tempfile

import edge_tts

from .config import settings

_locks: dict[str, asyncio.Lock] = {}


def _cache_key(text: str, voice: str) -> str:
    raw = f"{voice}|{text}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def audio_path(key: str) -> str:
    return os.path.join(settings.data_dir, "audio", f"{key}.ogg")


def audio_url(key: str) -> str:
    return f"{settings.public_base_url}/audio/{key}.ogg"


async def synth(text: str, voice: str | None = None) -> str:
    """合成 Ogg Opus（16k mono），返回缓存 key。命中缓存直接返回。"""
    voice = voice or settings.tts_voice
    key = _cache_key(text, voice)
    out_path = audio_path(key)
    if os.path.exists(out_path):
        return key

    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        if os.path.exists(out_path):
            return key

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        mp3_path = os.path.join(tempfile.gettempdir(), f"chan-{key}.mp3")

        try:
            await edge_tts.Communicate(text, voice=voice).save(mp3_path)
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                mp3_path,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "libopus",
                "-b:a",
                "32k",
                "-f",
                "ogg",
                out_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg failed: {stderr.decode(errors='replace')[:300]}")
        except Exception:
            if os.path.exists(out_path):
                os.unlink(out_path)
            raise
        finally:
            if os.path.exists(mp3_path):
                os.unlink(mp3_path)

    return key
