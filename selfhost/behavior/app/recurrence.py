"""备忘录循环规则：纯函数，方便单测。

约定：
- 所有时间戳都是 epoch 秒；展示/计算时转成服务器本地时区（默认 Asia/Shanghai）。
- 单次 = none；循环 = daily / weekly / monthly / weekdays。
- weekly 用「当前 due + 7 天」，monthly 用 repeat_day（原始日号，31 号会被压到
  当月最后一天，下个月仍回到 31 号）；weekdays 跳过周六日。
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

REPEAT_NONE = "none"
REPEAT_DAILY = "daily"
REPEAT_WEEKLY = "weekly"
REPEAT_MONTHLY = "monthly"
REPEAT_WEEKDAYS = "weekdays"

REPEAT_VALUES = (
    REPEAT_NONE,
    REPEAT_DAILY,
    REPEAT_WEEKLY,
    REPEAT_MONTHLY,
    REPEAT_WEEKDAYS,
)

REPEAT_LABELS = {
    REPEAT_NONE: "单次",
    REPEAT_DAILY: "每天",
    REPEAT_WEEKLY: "每周",
    REPEAT_MONTHLY: "每月",
    REPEAT_WEEKDAYS: "工作日",
}

WEEKDAY_CN = ("一", "二", "三", "四", "五", "六", "日")

# 足够覆盖 daily 十几年的追赶（月度/周度远小于这个数）
MAX_CATCH_UP = 5000


def get_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Asia/Shanghai")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Asia/Shanghai")


def parse_hhmm(value: str, fallback: tuple[int, int]) -> tuple[int, int]:
    try:
        hour_s, minute_s = str(value).split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (TypeError, ValueError):
        pass
    return fallback


def derive_rule_fields(start_at: float, rule: str, tz: ZoneInfo) -> tuple[str, int, int]:
    """从首次时间派生循环用字段：HH:MM / 星期几(0=周一) / 日号(1-31)。"""
    local = datetime.fromtimestamp(start_at, tz)
    repeat_time = f"{local.hour:02d}:{local.minute:02d}"
    return repeat_time, local.weekday(), local.day


def next_occurrence(
    due_at: float,
    rule: str,
    repeat_time: str,
    repeat_day: int | None,
    tz: ZoneInfo,
) -> float | None:
    if rule not in REPEAT_VALUES or rule == REPEAT_NONE:
        return None

    current = datetime.fromtimestamp(due_at, tz)
    hour, minute = parse_hhmm(repeat_time, (current.hour, current.minute))

    if rule == REPEAT_DAILY:
        next_date = current.date() + timedelta(days=1)
    elif rule == REPEAT_WEEKLY:
        next_date = current.date() + timedelta(days=7)
    elif rule == REPEAT_WEEKDAYS:
        next_date = current.date() + timedelta(days=1)
        while next_date.weekday() >= 5:  # 周六/周日
            next_date += timedelta(days=1)
    elif rule == REPEAT_MONTHLY:
        year, month = current.year, current.month + 1
        if month > 12:
            year, month = year + 1, 1
        day = repeat_day or current.day
        day = min(day, calendar.monthrange(year, month)[1])
        next_date = date(year, month, day)
    else:
        return None

    return datetime.combine(next_date, time(hour, minute), tzinfo=tz).timestamp()


def next_future_due(
    due_at: float,
    rule: str,
    repeat_time: str,
    repeat_day: int | None,
    tz: ZoneInfo,
    now: float,
    end_at: float | None = None,
) -> float | None:
    """从 due_at 起找到第一个 > now 的发生时间；超过 end_at 返回 None。"""
    due = due_at
    for _ in range(MAX_CATCH_UP):
        if due > now + 1:
            if end_at is not None and due > end_at:
                return None
            return due
        due = next_occurrence(due, rule, repeat_time, repeat_day, tz)
        if due is None:
            return None
    return None


def normalize_first_due(
    start_at: float,
    rule: str,
    repeat_time: str,
    repeat_day: int | None,
    tz: ZoneInfo,
    now: float,
    end_at: float | None = None,
) -> float:
    """校验/归一化首次时间。

    - 单次：必须晚于 now + 1 秒；
    - 循环：如果用户选的起点已经过去，自动推进到下一次（例如今天 10:00 设
      「每天 08:00」→ 明天 08:00），而不是立刻补一次。
    """
    if rule == REPEAT_NONE:
        if start_at <= now + 1:
            raise ValueError("time_in_past")
        if end_at is not None and start_at > end_at:
            raise ValueError("after_end")
        return start_at

    due = next_future_due(start_at, rule, repeat_time, repeat_day, tz, now, end_at)
    if due is None:
        raise ValueError("no_future_occurrence")
    return due


def repeat_label(rule: str, repeat_weekday: int | None, repeat_day: int | None) -> str:
    if rule == REPEAT_NONE:
        return REPEAT_LABELS[REPEAT_NONE]
    if rule == REPEAT_DAILY:
        return REPEAT_LABELS[REPEAT_DAILY]
    if rule == REPEAT_WEEKDAYS:
        return REPEAT_LABELS[REPEAT_WEEKDAYS]
    if rule == REPEAT_WEEKLY:
        if repeat_weekday is not None and 0 <= int(repeat_weekday) <= 6:
            return f"每周{WEEKDAY_CN[int(repeat_weekday)]}"
        return REPEAT_LABELS[REPEAT_WEEKLY]
    if rule == REPEAT_MONTHLY:
        if repeat_day is not None and 1 <= int(repeat_day) <= 31:
            return f"每月 {int(repeat_day)} 日"
        return REPEAT_LABELS[REPEAT_MONTHLY]
    return REPEAT_LABELS[REPEAT_NONE]
