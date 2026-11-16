"""pomodoro.py 服务端函数的回归测试（不需要 xiaozhi 容器）。

运行：
  .venv/bin/python -m unittest discover -s selfhost/tests -v
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "selfhost" / "patches" / "functions" / "pomodoro.py"


class FakeLogger:
    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class FakeAction:
    RESPONSE = "response"
    REQLLM = "reqllm"


class FakeToolType:
    SYSTEM_CTL = "system_ctl"


class FakeActionResponse:
    def __init__(self, action, result=None, response=None):
        self.action = action
        self.result = result
        self.response = response


def _load_plugin():
    """注入 config/plugins_func 桩，避免依赖 xiaozhi 镜像里的模块。"""
    config_pkg = types.ModuleType("config")
    config_pkg.__path__ = []
    logger_mod = types.ModuleType("config.logger")
    logger_mod.setup_logging = lambda: FakeLogger()
    config_pkg.logger = logger_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.logger"] = logger_mod

    plugins_pkg = types.ModuleType("plugins_func")
    plugins_pkg.__path__ = []
    register_mod = types.ModuleType("plugins_func.register")
    register_mod.register_function = lambda name, desc, type=None: (lambda fn: fn)
    register_mod.ToolType = FakeToolType
    register_mod.Action = FakeAction
    register_mod.ActionResponse = FakeActionResponse
    plugins_pkg.register = register_mod
    sys.modules["plugins_func"] = plugins_pkg
    sys.modules["plugins_func.register"] = register_mod

    spec = importlib.util.spec_from_file_location("stackchan_pomodoro", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class Msg:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class Dialogue:
    def __init__(self, user_text=None):
        self.dialogue = []
        if user_text is not None:
            self.dialogue.append(Msg("user", user_text))


class Conn:
    def __init__(self, config=None, user_text=None):
        self.config = config if config is not None else {
            "plugins": {
                "pomodoro": {
                    "base_url": "http://behavior.test",
                    "app_token": "token",
                    "timeout": 3,
                }
            }
        }
        self.device_id = "aa:bb:cc:dd:ee:ff"
        self.dialogue = Dialogue(user_text)


def set_user_text(conn, text):
    conn.dialogue.dialogue = [Msg("user", text)]


class PomodoroPluginTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_plugin()
        self.calls = []
        self.response = FakeResp(200, {"timer": {}})

        async def fake_post(base_url, app_token, timeout, *, device_id, minutes, task):
            self.calls.append(
                {
                    "base_url": base_url,
                    "app_token": app_token,
                    "timeout": timeout,
                    "device_id": device_id,
                    "minutes": minutes,
                    "task": task,
                }
            )
            return self.response

        self.mod._post_timer = fake_post

        self.active_calls = []
        self.action_calls = []
        self.active_response = FakeResp(
            200,
            {
                "timer": {
                    "job_id": "tmr-active",
                    "phase": "overtime",
                    "label": "专注",
                    "task": "",
                }
            },
        )
        self.action_response = FakeResp(200, {"id": "tmr-active", "status": "done"})

        async def fake_active(base_url, app_token, timeout, *, device_id):
            self.active_calls.append(
                {
                    "base_url": base_url,
                    "app_token": app_token,
                    "timeout": timeout,
                    "device_id": device_id,
                }
            )
            return self.active_response

        async def fake_action(base_url, app_token, timeout, *, job_id, action, announce):
            self.action_calls.append(
                {
                    "base_url": base_url,
                    "app_token": app_token,
                    "timeout": timeout,
                    "job_id": job_id,
                    "action": action,
                    "announce": announce,
                }
            )
            return self.action_response

        self.mod._get_active_timer = fake_active
        self.mod._post_timer_action = fake_action

    def run_fn(self, conn, **kwargs):
        return asyncio.run(self.mod.start_pomodoro(conn, **kwargs))

    def run_stop(self, conn, **kwargs):
        return asyncio.run(self.mod.stop_pomodoro(conn, **kwargs))

    def test_full_request_starts_and_normalizes_device_id(self):
        conn = Conn()
        r = self.run_fn(conn, duration_minutes=25, task="写周报")
        self.assertEqual(r.action, FakeAction.RESPONSE)
        self.assertIn("25 分钟", r.response)
        self.assertIn("写周报", r.response)
        self.assertEqual(self.calls[0]["device_id"], "aabbccddeeff")
        self.assertEqual(self.calls[0]["minutes"], 25)
        self.assertEqual(self.calls[0]["task"], "写周报")
        self.assertFalse(hasattr(conn, "_pomodoro_pending"))

    def test_missing_duration_asks_and_remembers_task(self):
        conn = Conn()
        r = self.run_fn(conn, task="写周报")
        self.assertEqual(r.action, FakeAction.RESPONSE)
        self.assertIn("写周报", r.response)
        self.assertIn("多久", r.response)
        self.assertEqual(self.calls, [])
        # 下一轮只给时长，任务名从 pending 接上
        r2 = self.run_fn(conn, duration_minutes=25)
        self.assertIn("写周报", r2.response)
        self.assertEqual(self.calls[0]["task"], "写周报")

    def test_guessed_duration_without_time_token_asks(self):
        """模型常把「开个番茄钟」猜成 25 分钟；用户原话没时长时必须先问。"""
        conn = Conn(user_text="开个番茄钟，我要写周报")
        r = self.run_fn(conn, duration_minutes=25, task="写周报")
        self.assertIn("多久", r.response)
        self.assertEqual(self.calls, [])
        self.assertEqual(conn._pomodoro_pending["task"], "写周报")

        # 下一轮用户只说「25 分钟」：这次原话有时长线索，允许开始
        set_user_text(conn, "25 分钟")
        r2 = self.run_fn(conn, duration_minutes=25)
        self.assertIn("25 分钟", r2.response)
        self.assertEqual(self.calls[0]["minutes"], 25)
        self.assertEqual(self.calls[0]["task"], "写周报")

    def test_explicit_duration_starts(self):
        conn = Conn(user_text="开始一个 25 分钟的番茄钟，写周报")
        r = self.run_fn(conn, duration_minutes=25, task="写周报")
        self.assertIn("25 分钟", r.response)
        self.assertEqual(len(self.calls), 1)

    def test_chinese_duration_hint_starts(self):
        conn = Conn(user_text="二十五分钟写周报")
        self.run_fn(conn, duration_minutes=25, task="写周报")
        self.assertEqual(len(self.calls), 1)

    def test_half_hour_hint_starts(self):
        conn = Conn(user_text="半小时番茄钟")
        self.run_fn(conn, duration_minutes=30)
        self.assertEqual(len(self.calls), 1)

    def test_label_is_not_task(self):
        """模型把固定标签「专注」当任务名传进来时，用户没说过就丢掉。"""
        conn = Conn(user_text="开始番茄钟")
        r = self.run_fn(conn, task="专注")
        self.assertIn("要专注多久", r.response)
        self.assertEqual(conn._pomodoro_pending["task"], "")

    def test_success_closes_session_after_reply(self):
        conn = Conn()
        self.assertFalse(getattr(conn, "close_after_chat", False))
        r = self.run_fn(conn, duration_minutes=25)
        self.assertIn("25 分钟", r.response)
        self.assertTrue(conn.close_after_chat)

    def test_missing_duration_does_not_close_session(self):
        conn = Conn()
        self.run_fn(conn, task="写周报")
        self.assertFalse(getattr(conn, "close_after_chat", False))

    def test_409_does_not_close_session(self):
        self.response = FakeResp(409, {"detail": "timer already running"})
        conn = Conn()
        self.run_fn(conn, duration_minutes=25)
        self.assertFalse(getattr(conn, "close_after_chat", False))

    def test_missing_task_is_allowed(self):
        conn = Conn()
        r = self.run_fn(conn, duration_minutes=25)
        self.assertIn("25 分钟", r.response)
        self.assertNotIn("None", r.response)
        self.assertEqual(self.calls[0]["task"], "")

    def test_invalid_duration_asks_again(self):
        conn = Conn()
        r = self.run_fn(conn, duration_minutes=300)
        self.assertIn("1 到 240", r.response)
        self.assertEqual(self.calls, [])

    def test_task_truncated_to_20(self):
        conn = Conn()
        self.run_fn(conn, duration_minutes=25, task="x" * 30)
        self.assertEqual(len(self.calls[0]["task"]), 20)

    def test_timer_already_running(self):
        self.response = FakeResp(409, {"detail": "timer already running"})
        r = self.run_fn(Conn(), duration_minutes=25)
        self.assertIn("已经有一个", r.response)

    def test_device_offline(self):
        self.response = FakeResp(409, {"detail": "device offline"})
        r = self.run_fn(Conn(), duration_minutes=25)
        self.assertIn("不在线", r.response)

    def test_missing_config(self):
        r = self.run_fn(Conn(config={}), duration_minutes=25)
        self.assertIn("还没有配置", r.response)
        self.assertEqual(self.calls, [])

    def test_timeout(self):
        import httpx

        async def fake_post(*args, **kwargs):
            raise httpx.TimeoutException("timeout")

        self.mod._post_timer = fake_post
        r = self.run_fn(Conn(), duration_minutes=25)
        self.assertIn("没响应", r.response)

    def test_stop_finishes_overtime_without_duplicate_announce(self):
        conn = Conn()
        r = self.run_stop(conn)
        self.assertEqual(r.action, FakeAction.RESPONSE)
        self.assertIn("关了", r.response)
        self.assertEqual(self.active_calls[0]["device_id"], "aabbccddeeff")
        self.assertEqual(self.action_calls[0]["job_id"], "tmr-active")
        self.assertEqual(self.action_calls[0]["action"], "finish")
        self.assertFalse(self.action_calls[0]["announce"])
        self.assertTrue(conn.close_after_chat)

    def test_stop_running_defaults_to_cancel(self):
        """还在倒计时时一句“关闭”按放弃处理，避免被算成提前完成。"""
        self.active_response = FakeResp(
            200,
            {
                "timer": {
                    "job_id": "tmr-active",
                    "phase": "focus",
                    "label": "专注",
                    "task": "",
                }
            },
        )
        conn = Conn()
        r = self.run_stop(conn)
        self.assertIn("放弃", r.response)
        self.assertEqual(self.action_calls[0]["action"], "cancel")
        self.assertTrue(conn.close_after_chat)

    def test_stop_explicit_finish_early(self):
        self.active_response = FakeResp(
            200,
            {
                "timer": {
                    "job_id": "tmr-active",
                    "phase": "focus",
                    "label": "专注",
                    "task": "",
                }
            },
        )
        conn = Conn()
        r = self.run_stop(conn, action="finish")
        self.assertIn("提前结束", r.response)
        self.assertEqual(self.action_calls[0]["action"], "finish")
        self.assertTrue(conn.close_after_chat)

    def test_stop_cancel_abandons_and_closes(self):
        conn = Conn()
        r = self.run_stop(conn, action="cancel")
        self.assertIn("放弃", r.response)
        self.assertEqual(self.action_calls[0]["action"], "cancel")
        self.assertFalse(self.action_calls[0]["announce"])
        self.assertTrue(conn.close_after_chat)

    def test_stop_without_active_timer_is_truthful(self):
        self.active_response = FakeResp(200, {"timer": None})
        conn = Conn()
        r = self.run_stop(conn)
        self.assertIn("没有在跑", r.response)
        self.assertEqual(self.action_calls, [])
        self.assertFalse(getattr(conn, "close_after_chat", False))

    def test_stop_clears_pending_start_draft(self):
        conn = Conn()
        self.run_fn(conn, task="写周报")
        self.assertTrue(hasattr(conn, "_pomodoro_pending"))
        self.run_stop(conn)
        self.assertFalse(hasattr(conn, "_pomodoro_pending"))

    def test_stop_active_query_timeout(self):
        import httpx

        async def fake_active(*args, **kwargs):
            raise httpx.TimeoutException("timeout")

        self.mod._get_active_timer = fake_active
        r = self.run_stop(Conn())
        self.assertIn("没响应", r.response)
        self.assertEqual(self.action_calls, [])

    def test_stop_post_timeout(self):
        import httpx

        async def fake_action(*args, **kwargs):
            raise httpx.TimeoutException("timeout")

        self.mod._post_timer_action = fake_action
        r = self.run_stop(Conn())
        self.assertIn("超时", r.response)

    def test_stop_missing_config(self):
        r = self.run_stop(Conn(config={}))
        self.assertIn("还没有配置", r.response)
        self.assertEqual(self.active_calls, [])


if __name__ == "__main__":
    unittest.main()
