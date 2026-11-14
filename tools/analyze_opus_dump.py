#!/usr/bin/env python3
"""解析服务端下发的 Opus dump，检查发送节奏与音频内容。

格式（2026-11-09 起）：[uint32 相对毫秒][uint16 包长][payload]
用途：区分「服务端没发/发晚了」和「设备没收下/没播」。

用法：
    python tools/analyze_opus_dump.py data/opus_dump/opus-xxx.bin
    python tools/analyze_opus_dump.py <file> --wav /tmp/out.wav
"""

import argparse
import struct
import sys
import wave

import numpy as np

try:
    import opuslib_next
except ImportError:
    opuslib_next = None


def parse(path: str):
    packets = []
    with open(path, "rb") as f:
        blob = f.read()
    off = 0
    while off + 6 <= len(blob):
        t_ms, size = struct.unpack_from("<IH", blob, off)
        off += 6
        if off + size > len(blob):
            print(f"[warn] 末尾包不完整：需要 {size} 字节，只剩 {len(blob) - off}")
            break
        packets.append((t_ms, blob[off:off + size]))
        off += size
    return packets


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--wav", help="把 dump 解码成 wav")
    ap.add_argument("--frame-ms", type=int, default=60)
    args = ap.parse_args()

    packets = parse(args.dump)
    if not packets:
        print("没有解析到任何包")
        return 1

    total_ms = len(packets) * args.frame_ms
    print(f"包数: {len(packets)}  音频时长: {total_ms / 1000:.2f}s")
    print(f"发送跨度: {packets[-1][0] / 1000:.2f}s（首包 t={packets[0][0]}ms）")

    gaps = [(packets[i][0] - packets[i - 1][0], i) for i in range(1, len(packets))]
    big = [g for g in gaps if g[0] > args.frame_ms * 1.8]
    print(f"最大间隔: {max(gaps)[0]}ms（第 {max(gaps)[1]} 包后）")
    if big:
        print("异常间隔（>1.8 帧）:")
        for g, idx in big[:10]:
            print(f"  第 {idx} 包后停了 {g}ms")
    else:
        print("间隔均匀，服务端发送节奏正常")

    if opuslib_next is None:
        print("\n[skip] 没装 opuslib_next，无法解码")
        return 0

    dec = opuslib_next.Decoder(16000, 1)
    pcm, bad = [], []
    for i, (_, payload) in enumerate(packets):
        try:
            pcm.append(np.frombuffer(dec.decode(payload, 960), dtype=np.int16))
        except Exception as e:  # noqa: BLE001
            bad.append((i, str(e)))
    if bad:
        print(f"解码失败 {len(bad)} 包: {bad[:3]}")
    if not pcm:
        return 1

    audio = np.concatenate(pcm)
    print(f"解码时长: {len(audio) / 16000:.2f}s")
    win = 320  # 20ms
    rms = [float(np.sqrt(np.mean(audio[i:i + win].astype(np.float64) ** 2)))
           for i in range(0, len(audio), win)]
    silent = [i for i, r in enumerate(rms) if r < 20]
    print(f"静音窗口(20ms): {len(silent)}/{len(rms)}")
    print("前 600ms RMS: " + " ".join("%.0f" % r for r in rms[:30]))

    if args.wav:
        with wave.open(args.wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(audio.tobytes())
        print(f"已写出 {args.wav}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
