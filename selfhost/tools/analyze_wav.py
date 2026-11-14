#!/usr/bin/env python3
"""粗略检查 16k 单声道 WAV：帧边界跳变、长零段、能量分布。"""
import sys
import wave
from array import array

path = sys.argv[1]
with wave.open(path, "rb") as w:
    assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
    samples = array("h")
    samples.frombytes(w.readframes(w.getnframes()))

n = len(samples)
frame = 960  # 60ms
diffs = [abs(samples[i] - samples[i - 1]) for i in range(1, n)]
diffs_sorted = sorted(diffs)
p99 = diffs_sorted[int(len(diffs_sorted) * 0.99)]
p999 = diffs_sorted[int(len(diffs_sorted) * 0.999)]
print(f"samples={n} duration={n/16000:.2f}s")
print(f"sample diff: p99={p99} p99.9={p999} max={max(diffs)} >8000={sum(1 for d in diffs if d > 8000)}")

boundary = []
for i in range(frame, n, frame):
    boundary.append(abs(samples[i] - samples[i - 1]))
boundary_sorted = sorted(boundary) or [0]
print(
    f"frame boundaries: n={len(boundary)} max={max(boundary_sorted)} "
    f"p99={boundary_sorted[int(len(boundary_sorted)*0.99)]} "
    f">8000={sum(1 for d in boundary if d > 8000)}"
)

# 长零段（>=10ms）
runs = []
start = None
for i, s in enumerate(samples):
    if s == 0:
        if start is None:
            start = i
    else:
        if start is not None:
            if i - start >= 160:
                runs.append((start / 16000, (i - start) / 16000))
            start = None
if start is not None and n - start >= 160:
    runs.append((start / 16000, (n - start) / 16000))
print(f"zero runs >=10ms: {len(runs)}")
for at, dur in runs[:10]:
    print(f"  {at:6.2f}s  {dur*1000:5.0f}ms")

# 每帧 RMS，找突发静音
rms = []
for i in range(0, n, frame):
    chunk = samples[i : i + frame]
    if not chunk:
        continue
    rms.append((sum(x * x for x in chunk) / len(chunk)) ** 0.5)
rms_sorted = sorted(rms)
print(
    f"frame rms: min={rms_sorted[0]:.0f} p5={rms_sorted[int(len(rms_sorted)*0.05)]:.0f} "
    f"median={rms_sorted[len(rms_sorted)//2]:.0f} max={rms_sorted[-1]:.0f}"
)
