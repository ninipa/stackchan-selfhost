"""备忘录循环规则的回归测试（纯函数，无需容器）。

运行：
  .venv/bin/python -m unittest discover -s selfhost/tests -v
"""
from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "selfhost" / "behavior" / "app" / "recurrence.py"

spec = importlib.util.spec_from_file_location("stackchan_recurrence", MODULE)
rec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rec)

TZ = ZoneInfo("Asia/Shanghai")


def ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=TZ).timestamp()


def local(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, TZ)


class RecurrenceTests(unittest.TestCase):
    def test_daily(self):
        due = ts(2026, 11, 16, 8, 0)
        nxt = rec.next_occurrence(due, rec.REPEAT_DAILY, "08:00", None, TZ)
        self.assertEqual(local(nxt), datetime(2026, 11, 17, 8, 0, tzinfo=TZ))

    def test_weekly(self):
        due = ts(2026, 11, 16, 8, 0)  # 周一
        nxt = rec.next_occurrence(due, rec.REPEAT_WEEKLY, "08:00", None, TZ)
        self.assertEqual(local(nxt), datetime(2026, 11, 23, 8, 0, tzinfo=TZ))
        self.assertEqual(local(nxt).weekday(), 0)

    def test_weekdays_skip_weekend(self):
        friday = ts(2026, 11, 20, 9, 30)
        nxt = rec.next_occurrence(friday, rec.REPEAT_WEEKDAYS, "09:30", None, TZ)
        self.assertEqual(local(nxt), datetime(2026, 11, 23, 9, 30, tzinfo=TZ))

    def test_monthly_clamps_to_last_day_but_keeps_31(self):
        jan31 = ts(2026, 1, 31, 8, 0)
        feb = rec.next_occurrence(jan31, rec.REPEAT_MONTHLY, "08:00", 31, TZ)
        self.assertEqual(local(feb), datetime(2026, 2, 28, 8, 0, tzinfo=TZ))
        mar = rec.next_occurrence(feb, rec.REPEAT_MONTHLY, "08:00", 31, TZ)
        self.assertEqual(local(mar), datetime(2026, 3, 31, 8, 0, tzinfo=TZ))

    def test_monthly_day30(self):
        jan30 = ts(2026, 1, 30, 8, 0)
        feb = rec.next_occurrence(jan30, rec.REPEAT_MONTHLY, "08:00", 30, TZ)
        self.assertEqual(local(feb), datetime(2026, 2, 28, 8, 0, tzinfo=TZ))
        mar = rec.next_occurrence(feb, rec.REPEAT_MONTHLY, "08:00", 30, TZ)
        self.assertEqual(local(mar), datetime(2026, 3, 30, 8, 0, tzinfo=TZ))

    def test_normalize_past_daily_to_tomorrow(self):
        now = ts(2026, 11, 16, 10, 0)
        start = ts(2026, 11, 16, 8, 0)
        due = rec.normalize_first_due(
            start, rec.REPEAT_DAILY, "08:00", None, TZ, now
        )
        self.assertEqual(local(due), datetime(2026, 11, 17, 8, 0, tzinfo=TZ))

    def test_normalize_future_stays(self):
        now = ts(2026, 11, 16, 10, 0)
        start = ts(2026, 11, 18, 8, 0)
        due = rec.normalize_first_due(
            start, rec.REPEAT_DAILY, "08:00", None, TZ, now
        )
        self.assertEqual(due, start)

    def test_one_time_past_rejected(self):
        now = ts(2026, 11, 16, 10, 0)
        with self.assertRaises(ValueError):
            rec.normalize_first_due(
                ts(2026, 11, 16, 9, 0), rec.REPEAT_NONE, "09:00", None, TZ, now
            )

    def test_end_at_cuts_recurrence(self):
        due = ts(2026, 11, 16, 8, 0)
        end = ts(2026, 11, 16, 23, 59)
        self.assertIsNone(
            rec.next_future_due(
                due, rec.REPEAT_DAILY, "08:00", None, TZ, due, end
            )
        )
        # 11-16 08:00 本身在 end 之前；下一次 11-17 超过 end → None
        self.assertIsNone(
            rec.next_future_due(
                due, rec.REPEAT_DAILY, "08:00", None, TZ, due + 60, end
            )
        )

    def test_next_future_catches_up_multiple_days(self):
        due = ts(2026, 11, 10, 8, 0)
        now = ts(2026, 11, 16, 10, 0)
        nxt = rec.next_future_due(
            due, rec.REPEAT_DAILY, "08:00", None, TZ, now
        )
        self.assertEqual(local(nxt), datetime(2026, 11, 17, 8, 0, tzinfo=TZ))

    def test_labels(self):
        self.assertEqual(rec.repeat_label(rec.REPEAT_NONE, None, None), "单次")
        self.assertEqual(rec.repeat_label(rec.REPEAT_DAILY, 0, 1), "每天")
        self.assertEqual(rec.repeat_label(rec.REPEAT_WEEKLY, 2, 1), "每周三")
        self.assertEqual(rec.repeat_label(rec.REPEAT_MONTHLY, 0, 15), "每月 15 日")
        self.assertEqual(rec.repeat_label(rec.REPEAT_WEEKDAYS, 0, 1), "工作日")

    def test_derive_fields(self):
        repeat_time, weekday, day = rec.derive_rule_fields(
            ts(2026, 11, 18, 7, 5), rec.REPEAT_WEEKLY, TZ
        )
        self.assertEqual(repeat_time, "07:05")
        self.assertEqual(weekday, 2)  # 周三
        self.assertEqual(day, 18)


if __name__ == "__main__":
    unittest.main()
