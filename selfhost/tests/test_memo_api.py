"""备忘录 API 的回归测试（FastAPI ASGI，无需起容器）。

运行：
  .venv/bin/python -m unittest discover -s selfhost/tests -v
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
BEHAVIOR = ROOT / "selfhost" / "behavior"

# 必须在导入 app 之前设置：settings/DB_PATH 在 import 时求值。
_TMP = tempfile.mkdtemp(prefix="chan-memo-test-")
os.environ["DATA_DIR"] = _TMP
os.environ["ALLOW_INSECURE_DEFAULTS"] = "1"
os.environ["APP_TOKEN"] = "a" * 64
os.environ["DEVICE_TOKEN"] = "d" * 64
os.environ["PUBLIC_BASE_URL"] = "http://test"
os.environ["TZ"] = "Asia/Shanghai"

sys.path.insert(0, str(BEHAVIOR))

import httpx  # noqa: E402

import app.main as main  # noqa: E402
from app import db, recurrence  # noqa: E402
from app.devices import DeviceConnection  # noqa: E402

H = {"X-App-Token": "a" * 64}
TZ = ZoneInfo("Asia/Shanghai")


class MemoApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 每个 IsolatedAsyncioTestCase 是独立事件循环；全局 Lock 要跟着换。
        db._lock = asyncio.Lock()
        await db.init_db()
        conn = db._db()
        await conn.execute("DELETE FROM memos")
        await conn.execute("DELETE FROM jobs")
        await conn.execute("DELETE FROM settings")
        await conn.commit()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await db.close_db()

    async def create(self, **overrides):
        body = {
            "device_id": "dev1",
            "title": "交作业",
            "detail": "",
            "due_at_ms": int((time.time() + 3600) * 1000),
            "repeat": "none",
        }
        body.update(overrides)
        res = await self.client.post("/api/memos", json=body, headers=H)
        return res

    async def test_memo_api_flow(self):
        # 1. 单次创建 + 列表
        res = await self.create()
        self.assertEqual(res.status_code, 200, res.text)
        memo = res.json()
        self.assertTrue(memo["enabled"])
        self.assertEqual(memo["repeat"], "none")
        self.assertEqual(memo["repeat_label"], "单次")
        memo_id = memo["id"]

        res = await self.client.get("/api/memos?device_id=dev1", headers=H)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()), 1)

        # 2. 全局提醒重复次数：默认 5，可改，越界拒绝
        res = await self.client.get("/api/settings", headers=H)
        self.assertEqual(res.json()["values"]["memo_repeat"], 5)
        res = await self.client.post(
            "/api/settings", json={"memo_repeat": 7}, headers=H
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["values"]["memo_repeat"], 7)
        res = await self.client.post(
            "/api/settings", json={"memo_repeat": 0}, headers=H
        )
        self.assertEqual(res.status_code, 400)

        # 3. 历史过滤：type=memo 只返回备忘录
        await db.create_job("job-msg", "dev1", "message", {"text": "hi"})
        await db.create_job("job-memo", "dev1", "memo", {"title": "交作业"})
        res = await self.client.get(
            "/api/jobs?type=memo&device_id=dev1", headers=H
        )
        self.assertEqual([j["id"] for j in res.json()], ["job-memo"])

        # 3. 编辑：标题/时间/详情
        new_due = int((time.time() + 7200) * 1000)
        res = await self.client.put(
            f"/api/memos/{memo_id}",
            json={
                "device_id": "dev1",
                "title": "交数学作业",
                "detail": "第 3 页",
                "due_at_ms": new_due,
                "repeat": "none",
            },
            headers=H,
        )
        self.assertEqual(res.status_code, 200, res.text)
        updated = res.json()
        self.assertEqual(updated["title"], "交数学作业")
        self.assertEqual(updated["detail"], "第 3 页")

        # 4. 暂停 / 恢复
        res = await self.client.post(
            f"/api/memos/{memo_id}/enabled", json={"enabled": False}, headers=H
        )
        paused = res.json()
        self.assertFalse(paused["enabled"])
        self.assertEqual(paused["last_status"], "paused")
        res = await self.client.post(
            f"/api/memos/{memo_id}/enabled", json={"enabled": True}, headers=H
        )
        self.assertTrue(res.json()["enabled"])
        self.assertIsNone(res.json()["last_status"])

        # 5. 完成
        res = await self.client.post(
            f"/api/memos/{memo_id}/complete", headers=H
        )
        done = res.json()
        self.assertFalse(done["enabled"])
        self.assertEqual(done["last_status"], "completed")

        # 6. 循环：起点在过去 → 归一化到下一次
        past = int((time.time() - 3600) * 1000)
        res = await self.create(title="每天喝水", due_at_ms=past, repeat="daily")
        self.assertEqual(res.status_code, 200, res.text)
        daily = res.json()
        self.assertEqual(daily["repeat_label"], "每天")
        self.assertGreater(daily["due_at"], time.time())
        start_local = datetime.fromtimestamp(daily["due_at"], TZ)
        self.assertEqual(
            f"{start_local.hour:02d}:{start_local.minute:02d}",
            datetime.fromtimestamp(past / 1000, TZ).strftime("%H:%M"),
        )

        # 7. 周期暂停后恢复：仍在未来，due 不倒退
        res = await self.client.post(
            f"/api/memos/{daily['id']}/enabled", json={"enabled": False}, headers=H
        )
        self.assertFalse(res.json()["enabled"])
        res = await self.client.post(
            f"/api/memos/{daily['id']}/enabled", json={"enabled": True}, headers=H
        )
        self.assertTrue(res.json()["enabled"])
        self.assertGreater(res.json()["due_at"], time.time())

        # 9. 单次过去时间 → 422
        res = await self.create(
            title="过去", due_at_ms=int((time.time() - 60) * 1000)
        )
        self.assertEqual(res.status_code, 422)

        # 10. 非法 repeat / 空标题 → 422
        res = await self.create(repeat="yearly")
        self.assertEqual(res.status_code, 422)
        res = await self.create(title="   ")
        self.assertEqual(res.status_code, 422)

        # 11. 删除
        res = await self.client.delete(f"/api/memos/{daily['id']}", headers=H)
        self.assertEqual(res.status_code, 200)
        res = await self.client.get("/api/memos?device_id=dev1", headers=H)
        ids = {m["id"] for m in res.json()}
        self.assertNotIn(daily["id"], ids)

        # 12. 鉴权
        res = await self.client.get("/api/memos")
        self.assertEqual(res.status_code, 401)

    async def test_timer_finish_can_skip_announce(self):
        class FakeWS:
            def __init__(self):
                self.sent = []

            async def send_json(self, payload):
                self.sent.append(("json", payload))

            async def send_bytes(self, data):
                self.sent.append(("bytes", data))

        ws = FakeWS()
        conn = DeviceConnection(device_id="dev-timer", ws=ws)
        main.registry.register(conn)
        try:
            # 提前完成：设备应收到 timer_complete，但 announce=false 不再推 TTS
            await db.create_job(
                "tmr-early",
                "dev-timer",
                "timer",
                {"duration_ms": 60000, "label": "专注", "task": ""},
                status="running",
            )
            res = await self.client.post(
                "/api/jobs/tmr-early/finish?announce=false", headers=H
            )
            self.assertEqual(res.status_code, 200, res.text)
            early = res.json()
            self.assertEqual(early["status"], "done")
            self.assertEqual(early["outcome"], "early")
            types = [p.get("type") for k, p in ws.sent if k == "json"]
            self.assertIn("timer_complete", types)
            self.assertFalse(any(k == "bytes" for k, _ in ws.sent))

            # 超时收工：设备收到 timer_cancel 清红字，同样不推 TTS
            await db.create_job(
                "tmr-late",
                "dev-timer",
                "timer",
                {"duration_ms": 1, "label": "专注", "task": ""},
                status="running",
            )
            await asyncio.sleep(0.01)
            res = await self.client.post(
                "/api/jobs/tmr-late/finish?announce=false", headers=H
            )
            self.assertEqual(res.status_code, 200, res.text)
            late = res.json()
            self.assertEqual(late["status"], "done")
            self.assertEqual(late["outcome"], "on_time")
            types = [p.get("type") for k, p in ws.sent if k == "json"]
            self.assertIn("timer_cancel", types)
            self.assertFalse(any(k == "bytes" for k, _ in ws.sent))
        finally:
            main.registry.unregister("dev-timer")

    async def test_memo_tick_fires_and_misses(self):
        class FakeWS:
            def __init__(self):
                self.sent = []

            async def send_json(self, payload):
                self.sent.append(("json", payload))

            async def send_bytes(self, data):
                self.sent.append(("bytes", data))

        ws = FakeWS()
        conn = DeviceConnection(device_id="dev-loop", ws=ws)
        main.registry.register(conn)
        audio_path = Path(_TMP) / "loop-test.ogg"
        audio_path.write_bytes(b"OggS-test")

        old_synth = main.tts.synth
        old_path = main.tts.audio_path
        old_url = main.tts.audio_url

        async def fake_synth(text, voice=None):
            return "loop-test"

        main.tts.synth = fake_synth
        main.tts.audio_path = lambda key: str(audio_path)
        main.tts.audio_url = lambda key: "http://test/audio/loop-test.ogg"
        try:
            now = time.time()
            await db.create_memo(
                "memo-one",
                "dev-loop",
                "喝水",
                "",
                now - 1,
                recurrence.REPEAT_NONE,
                "08:00",
                0,
                1,
                None,
            )
            await main._fire_due_memos()
            one = await db.get_memo("memo-one")
            self.assertFalse(one["enabled"])
            self.assertEqual(one["last_status"], "sent")

            kinds = [kind for kind, _ in ws.sent]
            self.assertIn("json", kinds)
            self.assertIn("bytes", kinds)
            behavior_msgs = [
                p for k, p in ws.sent if k == "json" and p.get("type") == "behavior"
            ]
            self.assertEqual(len(behavior_msgs), 1)
            script = behavior_msgs[0]["script"]
            self.assertEqual(script.get("kind"), "memo")
            self.assertTrue(script.get("stop_on_touch"))
            steps = script["steps"]
            says = [s for s in steps if s.get("op") == "say"]
            self.assertEqual(len(says), 5)
            self.assertIn("点屏幕停止", says[0]["text"])
            self.assertIn("提醒你，喝水", says[0]["text"])
            self.assertIn("audio_id", says[0])
            self.assertNotIn("audio_id", says[1])
            self.assertNotIn("audio_id", says[2])
            jobs = await db.list_jobs(limit=10, job_type="memo")
            self.assertEqual(jobs[0]["status"], "sent")

            # 点屏幕/语音优先打断 → stopped，不是 error
            await main._handle_device_message(
                conn,
                {
                    "type": "job_error",
                    "job_id": jobs[0]["id"],
                    "error": "voice_preempted",
                },
            )
            stopped = await db.get_job(jobs[0]["id"])
            self.assertEqual(stopped["status"], "stopped")

            # 循环：触发后 due 前进，enabled 保持
            await db.create_memo(
                "memo-daily",
                "dev-loop",
                "每天喝水",
                "",
                now - 1,
                recurrence.REPEAT_DAILY,
                "08:00",
                0,
                1,
                None,
            )
            await main._fire_due_memos()
            daily = await db.get_memo("memo-daily")
            self.assertTrue(daily["enabled"])
            self.assertGreater(daily["due_at"], now)
            self.assertEqual(daily["last_status"], "sent")

            # 清掉前面测试留下未 ack 的 memo job，避免阻塞并发用例
            for job in await db.list_jobs(limit=50, job_type="memo"):
                if job["status"] in ("sent", "running"):
                    await db.update_job(job["id"], status="done")

            # 同一设备两条同时到点：只发一条，第二条等前一个 job 结束
            await db.create_memo(
                "memo-c1",
                "dev-loop",
                "同时一",
                "",
                now - 1,
                recurrence.REPEAT_NONE,
                "08:00",
                0,
                1,
                None,
            )
            await db.create_memo(
                "memo-c2",
                "dev-loop",
                "同时二",
                "",
                now - 1,
                recurrence.REPEAT_NONE,
                "08:00",
                0,
                1,
                None,
            )
            await main._fire_due_memos()
            all_jobs = await db.list_jobs(limit=50, job_type="memo")
            c_jobs = [
                j
                for j in all_jobs
                if j["payload"].get("memo_id") in ("memo-c1", "memo-c2")
            ]
            self.assertEqual(len(c_jobs), 1)
            first = c_jobs[0]
            self.assertIn(first["status"], ("sent", "running"))
            # 模拟设备 ack 完成后，下一 tick 才发第二条
            await db.update_job(first["id"], status="done")
            await main._fire_due_memos()
            all_jobs = await db.list_jobs(limit=50, job_type="memo")
            c_jobs = [
                j
                for j in all_jobs
                if j["payload"].get("memo_id") in ("memo-c1", "memo-c2")
            ]
            self.assertEqual(len(c_jobs), 2)

            # 离线但在宽限内：保留 due，等下一次 tick
            await db.create_memo(
                "memo-wait",
                "dev-offline",
                "等设备",
                "",
                now - 1,
                recurrence.REPEAT_NONE,
                "08:00",
                0,
                1,
                None,
            )
            await main._fire_due_memos()
            wait = await db.get_memo("memo-wait")
            self.assertTrue(wait["enabled"])
            self.assertAlmostEqual(wait["due_at"], now - 1, places=3)
            self.assertIsNone(wait["last_status"])

            # 超过宽限：标 missed，单次关闭
            await db.create_memo(
                "memo-late",
                "dev-offline",
                "错过",
                "",
                now - main.settings.memo_grace_seconds - 60,
                recurrence.REPEAT_NONE,
                "08:00",
                0,
                1,
                None,
            )
            await main._fire_due_memos()
            late = await db.get_memo("memo-late")
            self.assertFalse(late["enabled"])
            self.assertEqual(late["last_status"], "missed")
            missed_jobs = [
                j
                for j in await db.list_jobs(limit=20, job_type="memo")
                if j["status"] == "missed"
            ]
            self.assertTrue(missed_jobs)
        finally:
            main.tts.synth = old_synth
            main.tts.audio_path = old_path
            main.tts.audio_url = old_url
            main.registry.unregister("dev-loop")


if __name__ == "__main__":
    unittest.main()
