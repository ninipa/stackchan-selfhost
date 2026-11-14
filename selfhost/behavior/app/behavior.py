"""把「发一条消息 ×N」编译成设备执行的行为脚本。

动作按 左转 → 点头 → 右转 循环，**每一遍配一次语音**：
    重复 1 次 → 左转 + 播一遍
    重复 3 次 → 左转 + 播、点头 + 播、右转 + 播
    重复 4 次 → 再回到左转

为什么不是让固件 say 的 repeat 循环：那个 repeat 只重复音频，动作在 say 外面只走一次
（2026-11-10 用户实测「重复 3 次但动作只做了一遍」）。改成「一遍动作 + 一遍语音」交错后，
节奏由脚本自己控制，不需要改固件。

音频取用：第 1 遍走 WS 预推的 audio_id（不下载）；后面几遍用 audio URL 现取——
设备的行为音频槽只有一个，take 之后就被清掉了，同一 id 不能复用（behavior_audio_take 会转移所有权）。
"""

from typing import Any

# ---- 姿态与速度（move 步的角度单位是 0.1°）----
HOME_PITCH = 200    # 俯仰常态位 20°：用户实测 31.5° 偏高、22° 也仍略高，**定为 20°**（2026-11-10）。
                    # 与上游 Stack-chan 的 default pose 一致。嫌低改 220（22°）。
                    # 注：固件的 pomodoro 仍用 kHomePitch（32.4°），两边统一在 pose-calibration 方案第 2 步。
NOD_PITCH = 60      # 点头低点 6°：200→60 是 14° 行程（2026-11-10 用户实测 8° 略高，改 6°）。
                    # 硬件 Y 轴限位 3°~87°，6° 只剩 3° 余量——别再往下压，否则可能顶到下限触发堵转保护。
TURN_YAW = 400      # 左右转 ±40°
MOVE_SPEED = 700    # 0~1000；原来 300，用户反馈「太慢」
SETTLE_MS = 60      # 动作停一下再开口，否则语音和舵机声糊在一起

# 用户面对机器人时看到的「左」。若实机方向反了，把这里改成 -1 即可。
TURN_SIGN = 1

BEATS = ("turn_left", "nod", "turn_right")
BEAT_NAMES = frozenset(BEATS)


def _beat_steps(kind: str) -> list[dict[str, Any]]:
    """一个动作 = 一到两个 move 步（点头是下去 + 回来）。"""
    if kind == "nod":
        return [
            {"op": "move", "yaw": 0, "pitch": NOD_PITCH, "speed": MOVE_SPEED},
            {"op": "move", "yaw": 0, "pitch": HOME_PITCH, "speed": MOVE_SPEED},
        ]
    direction = 1 if kind == "turn_left" else -1
    return [
        {
            "op": "move",
            "yaw": TURN_YAW * TURN_SIGN * direction,
            "pitch": HOME_PITCH,
            "speed": MOVE_SPEED,
        }
    ]


def build_message_script(
    job_id: str,
    text: str,
    audio_url: str,
    audio_id: str,
    audio_len: int,
    repeat: int,
    gap_ms: int,
    beats: list[str] | None = None,
) -> dict[str, Any]:
    """把「发一条消息 ×N」编译成设备执行的行为脚本。

    beats 可以指定动作序列（如 ["nod"] 只做点头），用于单独看某一个动作；
    不传就是默认的 左转 → 点头 → 右转 循环。
    """
    count = max(1, repeat)
    seq = [b for b in (beats or []) if b in BEAT_NAMES] or list(BEATS)

    steps: list[dict[str, Any]] = [
        {"op": "emotion", "emotion": "happy"},
        {"op": "led", "r": 0, "g": 40, "b": 0},
    ]

    for i in range(count):
        steps.extend(_beat_steps(seq[i % len(seq)]))
        steps.append({"op": "wait", "ms": SETTLE_MS})

        say: dict[str, Any] = {
            "op": "say",
            "audio": audio_url,
            "audio_len": audio_len,
            "text": text,
            "repeat": 1,
            "gap_ms": gap_ms,
        }
        if i == 0:
            # 第 1 遍用 WS 已经推下来的那份，省一次下载
            say["audio_id"] = audio_id
        steps.append(say)

    steps.extend(
        [
            # 收尾回正：不留着最后那个方向，下次唤醒从正面开始
            {"op": "move", "yaw": 0, "pitch": HOME_PITCH, "speed": 500},
            {"op": "emotion", "emotion": "neutral"},
            {"op": "led", "r": 0, "g": 0, "b": 0},
        ]
    )

    return {
        "v": 1,
        "job_id": job_id,
        "priority": "normal",
        "steps": steps,
    }
