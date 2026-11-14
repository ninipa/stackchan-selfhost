#!/usr/bin/env python3
"""把服务端 Opus dump（[uint16 len][opus packet]...）解码成 WAV。

用法（容器内，因为 opuslib_next 在服务端镜像里）：
    docker cp decode_opus_dump.py xiaozhi-esp32-server:/tmp/
    docker exec xiaozhi-esp32-server python /tmp/decode_opus_dump.py \
        /opt/xiaozhi-esp32-server/data/opus_dump/xxx.bin \
        /opt/xiaozhi-esp32-server/data/opus_dump/xxx.wav
"""

import struct
import sys
import wave

import opuslib_next


def main() -> int:
    src, dst = sys.argv[1], sys.argv[2]
    decoder = opuslib_next.Decoder(16000, 1)
    pcm = bytearray()

    with open(src, "rb") as f:
        while True:
            header = f.read(2)
            if len(header) < 2:
                break
            size = struct.unpack("<H", header)[0]
            packet = f.read(size)
            if len(packet) < size:
                break
            pcm += decoder.decode(packet, 960)

    with wave.open(dst, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)

    print(f"{src} -> {dst}  {len(pcm) // 2} samples")
    return 0


if __name__ == "__main__":
    sys.exit(main())
