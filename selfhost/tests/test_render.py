"""render.py 的回归测试（stdlib + 可选 PyYAML）。

运行：
  .venv/bin/python -m unittest discover -s selfhost/tests -v
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RENDER_PATH = ROOT / "selfhost" / "tools" / "render.py"

spec = importlib.util.spec_from_file_location("stackchan_render", RENDER_PATH)
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)

BASE_ENV = {
    "DEVICE_HOST": "192.168.1.20",
    "PUBLIC_BASE_URL": "https://chan.example.ts.net",
    "LLM_API_KEY": "sk-test-0123456789abcdef0123456789abcdef",
    "LLM_MODEL": "deepseek-chat",
    "LLM_BASE_URL": "https://api.deepseek.com/v1",
    "LLM_TEMPERATURE": "0.7",
    "LLM_MAX_TOKENS": "500",
    "QWEATHER_API_HOST": "",
    "QWEATHER_API_KEY": "",
    "DEFAULT_CITY": "北京",
    "TTS_VOICE": "zh-CN-XiaoyiNeural",
    "TZ": "Asia/Shanghai",
    "TZ_OFFSET": "+8",
    "APP_TOKEN": "0123456789abcdef" * 4,
    "DEVICE_TOKEN": "fedcba9876543210" * 4,
    "MEMORY": "nomem",
    "AUTH_ENABLED": "false",
    "ALLOWED_DEVICES": "",
    "ALLOW_INSECURE_DEFAULTS": "0",
    "LOG_LEVEL": "INFO",
    "END_PROMPT_TEXT": "我先去待机啦，下次聊",
    "PERSONA_FILE": "templates/persona.txt",
}


def env(**overrides):
    values = dict(BASE_ENV)
    values.update(overrides)
    return values


class RenderTests(unittest.TestCase):
    def test_valid_render(self):
        files = render.build(env(), allow_insecure=False)
        cfg = files["data/.config.yaml"]
        self.assertIn("ws://192.168.1.20:8000/xiaozhi/v1/", cfg)
        self.assertIn("voice: zh-CN-XiaoyiNeural", cfg)
        self.assertIn("Memory: nomem", cfg)
        self.assertNotIn("  mem_core:", cfg)
        self.assertIn("PUBLIC_BASE_URL=https://chan.example.ts.net", files["behavior/.env"])
        self.assertIn("TZ=Asia/Shanghai", files[".env"])
        self.assertNotIn("XIAOZHI_IMAGE=", files[".env"])
        self.assertTrue(files["data/agent-base-prompt.txt"].startswith("You are a playful"))

    def test_image_override_in_compose_env(self):
        files = render.build(env(XIAOZHI_IMAGE="ghcr.io/x/y@sha256:abc"), allow_insecure=False)
        self.assertIn("XIAOZHI_IMAGE=ghcr.io/x/y@sha256:abc", files[".env"])

    def test_tz_autodetect(self):
        values = env()
        values.pop("TZ")
        values.pop("TZ_OFFSET")
        files = render.build(values, allow_insecure=False)
        self.assertRegex(files[".env"], r"(?m)^TZ=\S+$")
        self.assertRegex(files["data/.config.yaml"], r"timezone_offset: [+-]\d")

    def test_mem_core_and_auth_allowlist(self):
        files = render.build(
            env(
                MEMORY="mem_core",
                AUTH_ENABLED="true",
                ALLOWED_DEVICES="aa:bb:cc:dd:ee:ff,11:22:33:44:55:66",
            ),
            allow_insecure=False,
        )
        cfg = files["data/.config.yaml"]
        self.assertIn("Memory: mem_core", cfg)
        self.assertIn("  mem_core:", cfg)
        self.assertIn('- "aa:bb:cc:dd:ee:ff"', cfg)
        self.assertIn('- "11:22:33:44:55:66"', cfg)

    def test_weak_token_rejected(self):
        with self.assertRaises(SystemExit):
            render.build(env(APP_TOKEN="short"), allow_insecure=False)

    def test_weak_token_allowed_explicitly(self):
        files = render.build(env(APP_TOKEN="short"), allow_insecure=True)
        self.assertIn("APP_TOKEN=short", files["behavior/.env"])
        self.assertIn("ALLOW_INSECURE_DEFAULTS=1", files["behavior/.env"])

    def test_bad_host_rejected(self):
        with self.assertRaises(SystemExit):
            render.build(env(DEVICE_HOST="http://192.168.1.20"), allow_insecure=False)

    def test_bad_tz_rejected(self):
        with self.assertRaises(SystemExit):
            render.build(env(TZ="Mars/Base"), allow_insecure=False)

    def test_check_is_idempotent(self):
        files = render.build(env(), allow_insecure=False)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            render.write_files(target, files)
            self.assertEqual(render.check_files(target, files), 0)
            # 再写一次：应生成备份，但内容仍然一致
            render.write_files(target, files)
            self.assertEqual(render.check_files(target, files), 0)
            backups = list(target.glob("data/.config.yaml.bak-*"))
            self.assertEqual(len(backups), 1)

    def test_firmware_snippet(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            render.firmware_snippet(env())
        out = buf.getvalue()
        self.assertIn('CONFIG_CHAN_SERVER_HOST="192.168.1.20"', out)
        self.assertIn("CONFIG_ESP_WIFI_STATIC_TX_BUFFER_NUM=8", out)
        self.assertIn("CONFIG_OTA_URL=", out)
        self.assertIn("CONFIG_CHAN_BEHAVIOR_WS_URL=", out)
        self.assertIn("CONFIG_CHAN_TELEOP_VIDEO_WS_URL=", out)

    def test_yaml_is_parseable(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML 未安装")
        files = render.build(env(MEMORY="mem_core"), allow_insecure=False)
        data = yaml.safe_load(files["data/.config.yaml"])
        self.assertEqual(data["server"]["websocket"], "ws://192.168.1.20:8000/xiaozhi/v1/")
        self.assertEqual(data["selected_module"]["Memory"], "mem_core")
        self.assertEqual(data["Memory"]["mem_core"]["type"], "mem_core")


if __name__ == "__main__":
    unittest.main()
