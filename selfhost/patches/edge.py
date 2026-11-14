import asyncio
import logging
import os
import shutil
import uuid
from datetime import datetime

import edge_tts

from core.providers.tts.base import TTSProviderBase

TAG = __name__


class TTSProvider(TTSProviderBase):
    TTS_PARAM_CONFIG = [
        ("ttsVolume", "volume", 0, 100, 50, int),
        ("ttsRate", "speech_rate", -100, 100, 0, int),
        ("ttsPitch", "pitch_rate", -100, 100, 0, int),
    ]

    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        if config.get("private_voice"):
            self.voice = config.get("private_voice")
        else:
            self.voice = config.get("voice")
        self.audio_file_type = config.get("format", "mp3")

        volume = config.get("volume", "50")
        self.volume = int(volume) if volume else 50

        speech_rate = config.get("rate", "0")
        self.speech_rate = int(speech_rate) if speech_rate else 0

        pitch_rate = config.get("pitch", "0")
        self.pitch_rate = int(pitch_rate) if pitch_rate else 0

        # 应用百分比调整
        self._apply_percentage_params(config)

        self.edge_rate = f"{self.speech_rate:+}%"
        self.edge_volume = f"{self.volume:+}%"
        self.edge_pitch = f"{self.pitch_rate:+}Hz"

    def generate_filename(self, extension=".mp3"):
        return os.path.join(
            self.output_file,
            f"tts-{datetime.now().date()}@{uuid.uuid4().hex}{extension}",
        )

    async def text_to_speak(self, text, output_file):
        try:
            communicate = edge_tts.Communicate(
                text,
                voice=self.voice,
                rate=self.edge_rate,
                volume=self.edge_volume,
                pitch=self.edge_pitch,
            )
            if output_file:
                # 确保目录存在并创建空文件
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                with open(output_file, "wb") as f:
                    pass

                # 流式写入音频数据
                with open(output_file, "ab") as f:  # 改为追加模式避免覆盖
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":  # 只处理音频数据块
                            f.write(chunk["data"])
            else:
                # 返回音频二进制数据
                audio_bytes = b""
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_bytes += chunk["data"]
                return audio_bytes
        except Exception as e:
            error_msg = f"Edge TTS请求失败: {e}"
            raise Exception(error_msg)  # 抛出异常，让调用方捕获

    async def stream_text_to_speak(self, text, opus_handler):
        """一次 EdgeTTS 请求，边收边解码边编码 Opus。

        与 text_to_speak 的区别：MP3 数据一到就喂给 ffmpeg，PCM 凑满 60ms
        就编码下发，不需要等整段音频生成完。整个回复共用同一个 Opus
        编码器（只有最后补一帧零），所以句间没有重连和补零接缝。

        ffmpeg 的 stdin/stdout 分别由 feeder 和本协程处理，避免管道写满死锁。
        """
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        sample_rate = self.conn.sample_rate
        proc = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "mp3",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )

        async def feed():
            """把 EdgeTTS 的 MP3 chunk 顺序写进 ffmpeg。"""
            try:
                communicate = edge_tts.Communicate(
                    text,
                    voice=self.voice,
                    rate=self.edge_rate,
                    volume=self.edge_volume,
                    pitch=self.edge_pitch,
                )
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        proc.stdin.write(chunk["data"])
                        await proc.stdin.drain()
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        feeder = asyncio.create_task(feed())
        frame_bytes = self.opus_encoder.total_frame_size * 2  # 960 samples * 2 bytes
        pending = bytearray()
        try:
            while True:
                if self.conn.client_abort:
                    break
                data = await proc.stdout.read(4096)
                if not data:
                    break
                pending.extend(data)
                while len(pending) >= frame_bytes:
                    frame = bytes(pending[:frame_bytes])
                    del pending[:frame_bytes]
                    self.opus_encoder.encode_pcm_to_opus_stream(
                        frame, end_of_stream=False, callback=opus_handler
                    )
            if pending and not self.conn.client_abort:
                pending.extend(b"\x00" * (frame_bytes - len(pending)))
                self.opus_encoder.encode_pcm_to_opus_stream(
                    bytes(pending), end_of_stream=True, callback=opus_handler
                )
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            if not feeder.done():
                feeder.cancel()
            try:
                await feeder
            except Exception as e:
                logging.warning(f"EdgeTTS 流式合成中断: {e}")
            try:
                await proc.wait()
            except Exception:
                pass
