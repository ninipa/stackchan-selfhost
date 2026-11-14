"""setup/migrate.py 的回归测试。"""
from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "selfhost"

OLD_CONFIG = """\
server:
  websocket: ws://192.168.1.60:8000/xiaozhi/v1/
  vision_explain: http://192.168.1.60:8003/mcp/vision/explain
  timezone_offset: +8
  auth:
    enabled: false
    allowed_devices: []
selected_module:
  LLM: DeepSeekLLM
  TTS: EdgeTTS
  Memory: mem_core
LLM:
  DeepSeekLLM:
    model_name: deepseek-chat
    base_url: https://api.deepseek.com/v1
    api_key: sk-old-key-0123456789
    temperature: 0.7
    max_tokens: 500
TTS:
  EdgeTTS:
    voice: zh-CN-XiaoyiNeural
plugins:
  get_weather:
    api_host: weather.example.com
    api_key: weather-key-123456
    default_location: 上海
log:
  log_level: INFO
end_prompt:
  text: 我先去待机啦，下次聊
prompt: |
  你是小智，旧人设。
  第二行。
"""


class MigrateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="chan-migrate-test-"))
        self.sh = self.tmp / "selfhost"
        (self.sh / "data").mkdir(parents=True)
        (self.sh / "behavior").mkdir()
        (self.sh / "templates").mkdir()
        (self.sh / "templates" / "persona.txt").write_text("默认人设\n", encoding="utf-8")
        (self.sh / "data" / ".config.yaml").write_text(OLD_CONFIG, encoding="utf-8")
        (self.sh / "behavior" / ".env").write_text(
            "PUBLIC_BASE_URL=https://chan.example.ts.net\n"
            f"APP_TOKEN={'a' * 64}\n"
            f"DEVICE_TOKEN={'b' * 64}\n"
            "TTS_VOICE=zh-CN-XiaoxiaoNeural\n",
            encoding="utf-8",
        )

        spec = importlib.util.spec_from_file_location(
            "chan_migrate_test", SRC / "setup" / "migrate.py"
        )
        self.mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.mod
        spec.loader.exec_module(self.mod)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_from_existing(self):
        env = self.mod.from_existing(self.sh)
        self.assertEqual(env["DEVICE_HOST"], "192.168.1.60")
        self.assertEqual(env["PUBLIC_BASE_URL"], "https://chan.example.ts.net")
        self.assertEqual(env["LLM_API_KEY"], "sk-old-key-0123456789")
        self.assertEqual(env["LLM_MODEL"], "deepseek-chat")
        self.assertEqual(env["LLM_BASE_URL"], "https://api.deepseek.com/v1")
        self.assertEqual(env["TTS_VOICE"], "zh-CN-XiaoyiNeural")
        self.assertEqual(env["QWEATHER_API_HOST"], "weather.example.com")
        self.assertEqual(env["QWEATHER_API_KEY"], "weather-key-123456")
        self.assertEqual(env["DEFAULT_CITY"], "上海")
        self.assertEqual(env["TZ"], "Asia/Shanghai")
        self.assertEqual(env["TZ_OFFSET"], "+8")
        self.assertEqual(env["AUTH_ENABLED"], "false")
        self.assertEqual(env["MEMORY"], "mem_core")
        self.assertEqual(env["APP_TOKEN"], "a" * 64)
        self.assertEqual(env["DEVICE_TOKEN"], "b" * 64)
        self.assertEqual(env["ALLOW_INSECURE_DEFAULTS"], "0")
        self.assertEqual(env["END_PROMPT_TEXT"], "我先去待机啦，下次聊")
        self.assertEqual(env["PERSONA_FILE"], "templates/persona.txt")
        self.assertEqual(env["COMPOSE_PROFILE"], "prod")

    def test_missing_config(self):
        self.assertEqual(self.mod.from_existing(self.tmp / "nope"), {})

    def test_short_device_token(self):
        (self.sh / "behavior" / ".env").write_text(
            "PUBLIC_BASE_URL=https://chan.example.ts.net\n"
            f"APP_TOKEN={'a' * 64}\n"
            "DEVICE_TOKEN=dev-device-token\n",
            encoding="utf-8",
        )
        env = self.mod.from_existing(self.sh)
        self.assertEqual(env["DEVICE_TOKEN"], "dev-device-token")
        self.assertEqual(env["ALLOW_INSECURE_DEFAULTS"], "1")

    def test_write_deploy(self):
        env = self.mod.write_deploy(self.sh, profile="dev")
        self.assertEqual(env["COMPOSE_PROFILE"], "dev")
        self.assertEqual(env["PERSONA_FILE"], "templates/persona.txt.migrated")

        deploy = self.sh / "deploy.env"
        self.assertTrue(deploy.is_file())
        self.assertEqual(deploy.stat().st_mode & 0o777, 0o600)
        text = deploy.read_text(encoding="utf-8")
        self.assertIn("DEVICE_HOST=192.168.1.60", text)
        self.assertIn("COMPOSE_PROFILE=dev", text)
        self.assertIn("PERSONA_FILE=templates/persona.txt.migrated", text)

        persona = self.sh / "templates" / "persona.txt.migrated"
        self.assertTrue(persona.is_file())
        self.assertIn("旧人设", persona.read_text(encoding="utf-8"))
        self.assertEqual(persona.stat().st_mode & 0o777, 0o600)

    def test_write_backs_up_existing(self):
        (self.sh / "deploy.env").write_text("OLD=1\n", encoding="utf-8")
        self.mod.write_deploy(self.sh, profile="prod")
        backups = list(self.sh.glob("deploy.env.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertIn("OLD=1", backups[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
