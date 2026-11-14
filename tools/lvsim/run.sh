#!/bin/bash
# 一条命令：编译 → 跑「行为场景」+「表情×状态矩阵」→ 出图 + 一份文字报告
# 用法：bash tools/lvsim/run.sh
#
# 两层：
#   1) 场景模式：跑真实 modifier（眨眼/呼吸/待机微表情/摇晃/摸头/说话，以及组合），
#      带状态转储和量化指标，能抓到"表情+修饰器"叠加出来的问题；
#   2) 矩阵模式：每个表情在 静息 / 说话 / 眨眼 三态下的几何。
set -e
WS="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$WS/build/lvsim"
mkdir -p "$WS/preview/lvsim"

bash "$WS/tools/lvsim/build.sh" >/dev/null

echo "== 场景（真实 modifier）=="
"$OUT/face_render" "$WS/preview/lvsim" scenario > "$WS/preview/lvsim_report.txt"
grep -E "eye_images" "$WS/preview/lvsim_report.txt" | head -60

echo
echo "== 矩阵（仅皮肤）=="
"$OUT/face_render" "$WS/preview/lvsim" matrix > /dev/null

cd "$WS"
PY_BIN="$WS/.venv/bin/python"
[ -x "$PY_BIN" ] || PY_BIN="$(command -v python3)"
"$PY_BIN" - <<'PY'
from PIL import Image, ImageDraw, ImageFont

base = "preview/lvsim"
font = None
for p in ("/Library/Fonts/Arial Unicode.ttf",
          "/System/Library/Fonts/Hiragino Sans GB.ttc"):
    try:
        font = ImageFont.truetype(p, 14)
        break
    except Exception:
        continue
if font is None:
    font = ImageFont.load_default()

W, H, LB = 320, 240, 24

def sheet(items, out, cols=3):
    rows = (len(items) + cols - 1) // cols
    img = Image.new("RGB", (W * cols, (H + LB) * rows), (18, 18, 20))
    d = ImageDraw.Draw(img)
    for i, (fn, label) in enumerate(items):
        im = Image.open(f"{base}/{fn}.ppm").convert("RGB")
        x, y = (i % cols) * W, (i // cols) * (H + LB)
        img.paste(im, (x, y))
        d.text((x + 8, y + H + 3), label, fill=(200, 200, 210), font=font)
    img.save(out)
    print(f"已生成 {out}  ({img.width}x{img.height})")

emotions = ["neutral","happy","laugh","angry","sad","doubt","sleepy","surprised","dizzy"]
states = [("rest","静息"),("talk","说话"),("blink","眨眼")]

# 1) 表情 × 状态：每 3 个表情一张图（3 列 = 3 个状态，3 行 = 3 个表情）
for i in range(0, len(emotions), 3):
    items = []
    for e in emotions[i:i+3]:
        for st, cn in states:
            items.append((f"{e}_{st}", f"{e} / {cn}"))
    sheet(items, f"{base}/../lvsim_matrix_{i//3 + 1}.png")

# 2) 9 个表情各自"说话中"的一张
items = [(f"speak_{e}_2", f"speak / {e}") for e in emotions]
sheet(items, f"{base}/../lvsim_speak_all.png")

# 3) 待机长采样（看微表情）
items = [(f"idle_{i}", f"idle {(i+1)*2.5:.0f}s") for i in range(10)]
sheet(items, f"{base}/../lvsim_idle.png", cols=5)

# 4) 组合场景
items = [
    ("pet_1", "摸头 +1.0s"),
    ("pet_3", "摸头 +2.0s"),
    ("pet_speak_2", "摸头→说话"),
    ("shake_0", "摇晃 +0.5s"),
    ("shake_3", "摇晃 +2.0s"),
    ("shake_speak_2", "摇晃→说话"),
]
sheet(items, f"{base}/../lvsim_combos.png")
PY
