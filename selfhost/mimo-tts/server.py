#!/usr/bin/env python3
"""MiMo TTS 适配层。

xiaozhi-esp32-server 的 CustomTTS 只会「POST 一段 JSON，把响应体当音频文件写盘」，
而 MiMo 返回的是 JSON + base64。这个小服务夹在中间做翻译：

    CustomTTS ──POST {"text","voice","style"}──> 本服务 ──> MiMo chat/completions
              <──── audio/wav 二进制 ────────────┘        <── base64 JSON

只依赖标准库，跑在宿主机上；容器通过 host.docker.internal:8899 访问。
密钥从环境变量 MIMO_API_KEY 读，不写进代码。

启动：./run.sh
"""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MIMO_URL = os.environ.get(
    "MIMO_API_URL", "https://api.xiaomimimo.com/v1/chat/completions"
)
MIMO_KEY = os.environ.get("MIMO_API_KEY", "")
MIMO_MODEL = os.environ.get("MIMO_MODEL", "mimo-v2.5-tts")
DEFAULT_VOICE = os.environ.get("MIMO_VOICE", "冰糖")
# 风格指令为空时的兜底：部分接口要求 messages 里至少有一轮 user
FALLBACK_STYLE = "请用自然、口语化的语气朗读下面这句话。"


def log(msg: str) -> None:
    print(f"[mimo-tts] {msg}", flush=True)


def synth(text: str, voice: str = "", style: str = "", instruction: str = "") -> bytes:
    """调 MiMo，返回 WAV 字节流。

    style 是「音频标签」，按官方要求拼在 assistant 文本开头（如 `(台湾腔)`），
    不朗读；instruction 是自然语言风格指令，放 user 轮（不朗读）。
    """
    if not MIMO_KEY:
        raise RuntimeError("MIMO_API_KEY 未设置")

    messages = []
    if instruction:
        messages.append({"role": "user", "content": instruction})
    spoken = f"{style}{text}" if style else text
    messages.append({"role": "assistant", "content": spoken})

    body = {
        "model": MIMO_MODEL,
        "messages": messages,
        "audio": {"format": "wav", "voice": voice or DEFAULT_VOICE},
        "stream": False,
    }

    def _post(payload: dict) -> dict:
        req = urllib.request.Request(
            MIMO_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {MIMO_KEY}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        data = _post(body)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        # 有些接口要求必须有 user 轮；补一句再试一次
        if e.code in (400, 422) and not instruction:
            log(f"首次请求 HTTP {e.code}，补 user 轮后重试：{detail}")
            body["messages"].insert(0, {"role": "user", "content": FALLBACK_STYLE})
            data = _post(body)
        else:
            raise RuntimeError(f"MiMo HTTP {e.code}: {detail}") from e

    try:
        audio_b64 = data["choices"][0]["message"]["audio"]["data"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"MiMo 响应里没有 audio.data：{str(data)[:500]}") from e

    return base64.b64decode(audio_b64)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):  # noqa: N802
        if self.path not in ("/tts", "/v1/audio/speech"):
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
            text = payload["text"]
            voice = payload.get("voice", "")
            style = payload.get("style", "")
            instruction = payload.get("instruction", "")
        except (json.JSONDecodeError, KeyError) as e:
            self._send(400, f"bad request: {e}".encode(), "text/plain")
            return

        started = time.monotonic()
        try:
            audio = synth(text, voice, style, instruction)
        except Exception as e:  # noqa: BLE001
            log(f"FAIL text={text[:30]!r} voice={voice}: {e}")
            self._send(502, str(e).encode("utf-8", "replace"), "text/plain")
            return

        elapsed = (time.monotonic() - started) * 1000
        log(
            f"OK {len(text)}字 voice={voice or DEFAULT_VOICE} style={style or '-'} "
            f"{len(audio)}B {elapsed:.0f}ms text={text[:30]!r}"
        )
        self._send(200, audio, "audio/wav")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # 静音默认的每条访问日志
        pass


def main() -> None:
    host = os.environ.get("MIMO_BIND", "0.0.0.0")
    port = int(os.environ.get("MIMO_PORT", "8899"))
    if not MIMO_KEY:
        log("警告：MIMO_API_KEY 未设置，所有请求都会失败")
    log(f"listening on http://{host}:{port}  model={MIMO_MODEL} url={MIMO_URL}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
