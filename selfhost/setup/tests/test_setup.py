"""setup/app.py 的回归测试（dry-run，不碰 Docker）。

运行：
  .venv/bin/python -m unittest discover -s selfhost/setup/tests -v
"""
from __future__ import annotations

import importlib.util
import gzip
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "selfhost"


class SetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="chan-setup-test-"))
        cls.sh = cls.tmp / "selfhost"
        (cls.sh / "tools").mkdir(parents=True)
        shutil.copy2(SRC / "tools" / "render.py", cls.sh / "tools" / "render.py")
        shutil.copytree(SRC / "templates", cls.sh / "templates")
        shutil.copy2(SRC / "deploy.env.example", cls.sh / "deploy.env.example")
        (cls.sh / "data").mkdir()
        (cls.sh / "behavior").mkdir()

        os.environ["CHAN_SELFHOST_DIR"] = str(cls.sh)
        os.environ["SETUP_DRY_RUN"] = "1"
        os.environ["SETUP_REQUIRE_TOKEN"] = "1"

        spec = importlib.util.spec_from_file_location(
            "chan_setup_test_app", SRC / "setup" / "app.py"
        )
        cls.appmod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.appmod
        spec.loader.exec_module(cls.appmod)

        from fastapi.testclient import TestClient

        cls.client = TestClient(cls.appmod.app)
        cls.admin = (cls.sh / "data" / "setup-admin.token").read_text().strip()
        cls.headers = {"X-Admin-Token": cls.admin}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def payload(self) -> dict:
        return {
            "values": {
                "DEVICE_HOST": "192.168.1.50",
                "PUBLIC_BASE_URL": "https://chan.example.ts.net",
                "COMPOSE_PROFILE": "prod",
                "LLM_BASE_URL": "https://api.deepseek.com/v1",
                "LLM_MODEL": "deepseek-chat",
                "LLM_TEMPERATURE": "0.7",
                "LLM_MAX_TOKENS": "500",
                "QWEATHER_API_HOST": "",
                "DEFAULT_CITY": "北京",
                "TTS_VOICE": "zh-CN-XiaoyiNeural",
                "TZ": "Asia/Shanghai",
                "TZ_OFFSET": "+8",
                "END_PROMPT_TEXT": "我先去待机啦，下次聊",
                "MEMORY": "nomem",
                "LOG_LEVEL": "INFO",
                "AUTH_ENABLED": "false",
                "ALLOWED_DEVICES": "",
            },
            "secrets": {
                "LLM_API_KEY": "sk-test-0123456789abcdef0123456789abcdef",
                "APP_TOKEN": "a" * 64,
                "DEVICE_TOKEN": "b" * 64,
            },
            "persona": "你是小智，测试人设。",
        }

    def setUp(self):
        # 每个用例都从一份有效配置开始（dry-run 只写文件）
        res = self.client.post("/api/config", headers=self.headers, json=self.payload())
        self.assertEqual(res.status_code, 200, res.text)

    # ------------------------------------------------------------------ #

    def test_00_session_required(self):
        res = self.client.get("/api/session")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["admin_required"])

    def test_01_auth_required(self):
        self.assertEqual(self.client.get("/api/config").status_code, 401)
        self.assertEqual(
            self.client.get("/api/config", headers={"X-Admin-Token": "bad"}).status_code, 401
        )
        self.assertEqual(self.client.get("/api/config", headers=self.headers).status_code, 200)

    def test_02_save_renders_and_masks(self):
        res = self.client.post("/api/config", headers=self.headers, json=self.payload())
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body["dry_run"])
        self.assertIn("data/.config.yaml", body["files"])

        env_text = (self.sh / "deploy.env").read_text(encoding="utf-8")
        self.assertIn("LLM_API_KEY=sk-test-0123456789abcdef0123456789abcdef", env_text)
        self.assertIn("DEVICE_HOST=192.168.1.50", env_text)

        cfg = (self.sh / "data" / ".config.yaml").read_text(encoding="utf-8")
        self.assertIn("ws://192.168.1.50:8000/xiaozhi/v1/", cfg)
        self.assertIn("api_key: sk-test-0123456789abcdef0123456789abcdef", cfg)
        self.assertIn("voice: zh-CN-XiaoyiNeural", cfg)

        persona = (self.sh / "templates" / "persona.txt").read_text(encoding="utf-8")
        self.assertIn("测试人设", persona)

        data = self.client.get("/api/config", headers=self.headers).json()
        self.assertEqual(data["values"]["DEVICE_HOST"], "192.168.1.50")
        self.assertEqual(data["values"]["COMPOSE_PROFILE"], "prod")
        self.assertNotEqual(
            data["secrets"]["LLM_API_KEY"], "sk-test-0123456789abcdef0123456789abcdef"
        )
        self.assertTrue(data["secrets"]["LLM_API_KEY"].startswith("sk-"))
        self.assertEqual(data["firmware"]["token"], "b" * 64)
        self.assertEqual(data["pairing"]["token"], "a" * 64)
        self.assertIn("Asia/Shanghai", data["timezones"]["common"])
        self.assertIn("Asia/Shanghai", data["timezones"]["all"])
        self.assertEqual(self.appmod.DEFAULT_VALUES["LLM_MODEL"], "deepseek-flash")

    def test_03_empty_secret_keeps_existing(self):
        p = self.payload()
        p["secrets"] = {}
        res = self.client.post("/api/config", headers=self.headers, json=p)
        self.assertEqual(res.status_code, 200, res.text)
        env_text = (self.sh / "deploy.env").read_text(encoding="utf-8")
        self.assertIn("LLM_API_KEY=sk-test-0123456789abcdef0123456789abcdef", env_text)
        self.assertIn("APP_TOKEN=" + "a" * 64, env_text)
        self.assertIn("DEVICE_TOKEN=" + "b" * 64, env_text)

    def test_04_validation(self):
        cases = []
        p = self.payload()
        p["values"]["DEVICE_HOST"] = "http://x"
        cases.append(p)
        p = self.payload()
        p["values"]["TZ"] = "Mars/Base"
        cases.append(p)
        p = self.payload()
        p["values"]["COMPOSE_PROFILE"] = "weird"
        cases.append(p)
        p = self.payload()
        p["persona"] = ""
        cases.append(p)
        for case in cases:
            res = self.client.post("/api/config", headers=self.headers, json=case)
            self.assertEqual(res.status_code, 400, res.text)

        with self.assertRaises(HTTPException):
            self.appmod._merge_payload({}, {**self.payload(), "secrets": {}})

    def test_05_file_modes(self):
        for path in (self.sh / "deploy.env", self.sh / "data" / "setup-admin.token"):
            self.assertTrue(path.is_file())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600, str(path))

    def test_06_status_dry_run(self):
        res = self.client.get("/api/status", headers=self.headers)
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body["dry_run"])
        self.assertEqual(body["containers"], [])

    def test_07_test_endpoint_validation(self):
        res = self.client.post(
            "/api/test/weather",
            headers=self.headers,
            json={"api_host": "", "api_key": "", "city": "北京"},
        )
        self.assertEqual(res.status_code, 400, res.text)


    def test_08_rollback_restores_files(self):
        before_env = (self.sh / "deploy.env").read_text(encoding="utf-8")
        before_cfg = (self.sh / "data" / ".config.yaml").read_text(encoding="utf-8")
        before_persona = (self.sh / "templates" / "persona.txt").read_text(encoding="utf-8")

        p = self.payload()
        p["values"]["DEVICE_HOST"] = "192.168.1.99"
        p["persona"] = "ROLLBACK 人设"

        original = self.appmod._restart_services
        self.appmod.DRY_RUN = False
        self.appmod._restart_services = lambda profile: (_ for _ in ()).throw(
            RuntimeError("boom")
        )
        try:
            res = self.client.post("/api/config", headers=self.headers, json=p)
            self.assertEqual(res.status_code, 500, res.text)
            self.assertIn("已回滚", res.text)
        finally:
            self.appmod._restart_services = original
            self.appmod.DRY_RUN = True

        self.assertEqual((self.sh / "deploy.env").read_text(encoding="utf-8"), before_env)
        self.assertEqual((self.sh / "data" / ".config.yaml").read_text(encoding="utf-8"), before_cfg)
        self.assertEqual(
            (self.sh / "templates" / "persona.txt").read_text(encoding="utf-8"), before_persona
        )
        self.assertNotIn("192.168.1.99", (self.sh / "deploy.env").read_text(encoding="utf-8"))


    def test_09_restart_command_sequence(self):
        log = self.tmp / "docker.log"
        fake = self.sh / "bin" / "docker"
        fake.parent.mkdir(parents=True, exist_ok=True)
        fake.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit 0\n', encoding="utf-8")
        fake.chmod(0o755)

        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{fake.parent}:{old_path}"
        original_probe = self.appmod._probe
        self.appmod._probe = lambda host: {"ok": True}
        self.appmod.DRY_RUN = False
        try:
            res = self.client.post("/api/config", headers=self.headers, json=self.payload())
            self.assertEqual(res.status_code, 200, res.text)
            text = log.read_text(encoding="utf-8")
            self.assertIn("xiaozhi-esp32-server", text)
            self.assertIn("chan-behavior", text)
            self.assertNotIn("docker-compose.dev.yml", text)

            p = self.payload()
            p["values"]["COMPOSE_PROFILE"] = "dev"
            res = self.client.post("/api/config", headers=self.headers, json=p)
            self.assertEqual(res.status_code, 200, res.text)
            self.assertIn("docker-compose.dev.yml", log.read_text(encoding="utf-8"))
        finally:
            self.appmod._probe = original_probe
            self.appmod.DRY_RUN = True
            os.environ["PATH"] = old_path


    def test_10_legacy_device_token_allowed(self):
        deploy = self.sh / "deploy.env"
        text = deploy.read_text(encoding="utf-8")
        text = text.replace("DEVICE_TOKEN=" + "b" * 64, "DEVICE_TOKEN=dev-device-token")
        deploy.write_text(text, encoding="utf-8")

        p = self.payload()
        p["secrets"] = {}
        res = self.client.post("/api/config", headers=self.headers, json=p)
        self.assertEqual(res.status_code, 200, res.text)
        text = deploy.read_text(encoding="utf-8")
        self.assertIn("DEVICE_TOKEN=dev-device-token", text)
        self.assertIn("ALLOW_INSECURE_DEFAULTS=1", text)


    def test_11_local_mode_no_token(self):
        old = self.appmod.REQUIRE_TOKEN
        self.appmod.REQUIRE_TOKEN = False
        try:
            res = self.client.get("/api/config")
            self.assertEqual(res.status_code, 200, res.text)
            session = self.client.get("/api/session").json()
            self.assertFalse(session["admin_required"])
        finally:
            self.appmod.REQUIRE_TOKEN = old


    def test_12_compose_profile_not_required(self):
        p = self.payload()
        p["values"].pop("COMPOSE_PROFILE", None)
        p["secrets"] = {}
        res = self.client.post("/api/config", headers=self.headers, json=p)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn(
            "COMPOSE_PROFILE=prod", (self.sh / "deploy.env").read_text(encoding="utf-8")
        )


    def test_13_llm_url(self):
        p = self.payload()
        p["values"]["LLM_BASE_URL"] = "https://api.deepseek.com/v1/chat/completions"
        p["secrets"] = {}
        res = self.client.post("/api/config", headers=self.headers, json=p)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn(
            "LLM_BASE_URL=https://api.deepseek.com/v1",
            (self.sh / "deploy.env").read_text(encoding="utf-8"),
        )
        data = self.client.get("/api/config", headers=self.headers).json()
        self.assertEqual(
            data["values"]["LLM_BASE_URL"],
            "https://api.deepseek.com/v1/chat/completions",
        )

        p = self.payload()
        p["values"]["LLM_BASE_URL"] = "https://api.openai.com/v1/responses"
        res = self.client.post("/api/config", headers=self.headers, json=p)
        self.assertEqual(res.status_code, 400, res.text)

    def test_14_tokens_and_tz_autogenerated(self):
        p = self.payload()
        p["values"]["TZ"] = ""
        p["secrets"] = {"LLM_API_KEY": "sk-test-0123456789abcdef0123456789abcdef"}
        merged, _ = self.appmod._merge_payload({}, p)
        self.assertEqual(len(merged["APP_TOKEN"]), 64)
        self.assertEqual(len(merged["DEVICE_TOKEN"]), 64)
        self.assertEqual(merged["ALLOW_INSECURE_DEFAULTS"], "0")
        self.assertTrue(merged["TZ"])
        self.assertRegex(merged["TZ_OFFSET"], r"^[+-]\d{1,2}(?:\.\d{1,2})?$")

    def test_15_http_json_handles_gzip(self):
        class FakeResp:
            status = 200
            headers = {"Content-Encoding": "gzip"}

            def read(self):
                return gzip.compress(b'{"ok": true}')

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        seen = {}
        original = self.appmod.urllib.request.urlopen

        def fake(req, timeout):
            seen["encoding"] = req.get_header("Accept-encoding")
            return FakeResp()

        self.appmod.urllib.request.urlopen = fake
        try:
            self.assertEqual(self.appmod._http_json("https://example.com/x"), {"ok": True})
        finally:
            self.appmod.urllib.request.urlopen = original
        self.assertEqual(seen["encoding"], "identity")


if __name__ == "__main__":
    unittest.main()
