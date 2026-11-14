"""StackChan 服务端配置页（宿主机 FastAPI，默认 127.0.0.1:8200）。

设计边界：
- 独立 ADMIN_TOKEN，和 App/DEVICE token 完全分开；
- provider key 只写不读：GET 只返回掩码，POST 空值 = 保留原值；
- 保存 → 备份 → 原子写 → render → 重启容器 → 健康检查 → 失败回滚；
- 不挂 docker.sock：用当前用户的 `docker` CLI；
- SETUP_DRY_RUN=1 时只写文件，不重启（测试/预览用）。
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[2]          # repo root
SELFHOST = Path(os.environ.get("CHAN_SELFHOST_DIR", ROOT / "selfhost"))
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DEPLOY_ENV = SELFHOST / "deploy.env"
PERSONA = SELFHOST / "templates" / "persona.txt"
ADMIN_TOKEN_FILE = SELFHOST / "data" / "setup-admin.token"
DRY_RUN = os.environ.get("SETUP_DRY_RUN", "0").strip() == "1"
REQUIRE_TOKEN = os.environ.get("SETUP_REQUIRE_TOKEN", "0").strip() == "1"
PORT = int(os.environ.get("SETUP_PORT", "8200"))

sys.path.insert(0, str(APP_DIR))
from migrate import from_existing  # noqa: E402

SECRET_KEYS = ("LLM_API_KEY", "QWEATHER_API_KEY", "APP_TOKEN", "DEVICE_TOKEN")
SECRET_HINTS = {
    "LLM_API_KEY": "sk-…",
    "QWEATHER_API_KEY": "和风 key",
    "APP_TOKEN": "App Token",
    "DEVICE_TOKEN": "Device Token",
}
VALUE_KEYS = (
    "DEVICE_HOST",
    "PUBLIC_BASE_URL",
    "LLM_MODEL",
    "LLM_BASE_URL",
    "LLM_TEMPERATURE",
    "LLM_MAX_TOKENS",
    "QWEATHER_API_HOST",
    "DEFAULT_CITY",
    "TTS_VOICE",
    "TZ",
    "TZ_OFFSET",
    "MEMORY",
    "LOG_LEVEL",
    "AUTH_ENABLED",
    "ALLOWED_DEVICES",
    "END_PROMPT_TEXT",
    "COMPOSE_PROFILE",
)
DEFAULT_VALUES = {
    "LLM_MODEL": "deepseek-flash",
    "LLM_BASE_URL": "https://api.deepseek.com/v1",
    "LLM_TEMPERATURE": "0.7",
    "LLM_MAX_TOKENS": "500",
    "DEFAULT_CITY": "北京",
    "TTS_VOICE": "zh-CN-XiaoyiNeural",
    "MEMORY": "nomem",
    "LOG_LEVEL": "INFO",
    "AUTH_ENABLED": "false",
    "ALLOWED_DEVICES": "",
    "END_PROMPT_TEXT": "我先去待机啦，下次聊",
    "COMPOSE_PROFILE": "prod",
}


def _render_module():
    spec = importlib.util.spec_from_file_location(
        "chan_setup_render", SELFHOST / "tools" / "render.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"找不到 {SELFHOST / 'tools' / 'render.py'}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render = _render_module()


def _write_atomic(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _ensure_admin_token() -> str:
    ADMIN_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if ADMIN_TOKEN_FILE.is_file():
        token = ADMIN_TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_hex(32)
    _write_atomic(ADMIN_TOKEN_FILE, token + "\n")
    if REQUIRE_TOKEN:
        print(f"[chan-setup] admin token: {token}", flush=True)
        print(f"[chan-setup] token file: {ADMIN_TOKEN_FILE}", flush=True)
    return token


ADMIN_TOKEN = _ensure_admin_token()


def require_admin(x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> None:
    # 默认只绑 127.0.0.1，本机用户就是管理员；只有显式 SETUP_REQUIRE_TOKEN=1 才校验。
    if not REQUIRE_TOKEN:
        return
    if not x_admin_token or not secrets.compare_digest(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="admin token 不正确")


def _load_env() -> dict[str, str]:
    if DEPLOY_ENV.is_file():
        try:
            return render.parse_env(DEPLOY_ENV)
        except SystemExit:
            pass
    migrated = from_existing(SELFHOST)
    if migrated:
        return migrated
    example = SELFHOST / "deploy.env.example"
    if example.is_file():
        try:
            return render.parse_env(example)
        except SystemExit:
            pass
    return {}


def _is_placeholder(value: Optional[str]) -> bool:
    return not value or value.strip().startswith("__REPLACE_")


def _mask(value: Optional[str]) -> str:
    value = (value or "").strip()
    if _is_placeholder(value):
        return ""
    if len(value) <= 8:
        return "…" + value[-2:]
    return value[:3] + "…" + value[-4:]


def _clean_value(env: dict[str, str], key: str) -> str:
    value = env.get(key, DEFAULT_VALUES.get(key, ""))
    return "" if _is_placeholder(value) else (value or "")


def _persona_path(env: dict[str, str]) -> Path:
    rel = env.get("PERSONA_FILE") or "templates/persona.txt"
    path = SELFHOST / rel
    return path if path.is_file() else PERSONA


def _read_persona(env: Optional[dict[str, str]] = None) -> str:
    values = env or _load_env()
    path = _persona_path(values)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def _validate_host(value: str) -> str:
    value = (value or "").strip()
    if "://" in value or "/" in value or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]*", value):
        raise HTTPException(status_code=400, detail=f"DEVICE_HOST 只支持 IP/主机名：{value!r}")
    return value


def _validate_base_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail=f"PUBLIC_BASE_URL 必须以 http(s):// 开头：{value!r}")
    return value


def _llm_chat_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        raise HTTPException(status_code=400, detail="LLM API URL 不能为空")
    if value.endswith("/chat/completions"):
        return value
    if re.search(r"/(responses|messages|completions)$", value):
        raise HTTPException(
            status_code=400,
            detail="只支持 OpenAI Chat Completions 兼容端点（URL 以 /chat/completions 结尾）；"
            "Responses/Anthropic/Gemini 需要另加 provider 适配",
        )
    return value + "/chat/completions"


def _llm_base_url(value: str) -> str:
    # 服务端 openai provider 用的是 base_url + SDK 自动拼 /chat/completions；
    # UI 输入完整端点，这里归一化成 base。
    return _llm_chat_url(value)[: -len("/chat/completions")].rstrip("/")


def _llm_display_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    if value.endswith("/chat/completions"):
        return value
    return value + "/chat/completions"


def _validate_tz(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="TZ 不能为空")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=400, detail=f"TZ 不是合法时区：{value!r}")
    return value


def _validate_offset(value: str) -> str:
    if not re.fullmatch(r"[+-]\d{1,2}(?:\.\d{1,2})?", value or ""):
        raise HTTPException(status_code=400, detail=f"TZ_OFFSET 格式应为 +8 / -5：{value!r}")
    return value


def _detect_tz() -> str:
    candidate = os.environ.get("TZ", "").strip()
    if candidate:
        try:
            ZoneInfo(candidate)
            return candidate
        except (ZoneInfoNotFoundError, ValueError):
            pass
    try:
        target = Path("/etc/localtime").resolve()
        parts = target.parts
        if "zoneinfo" in parts:
            idx = len(parts) - 1 - parts[::-1].index("zoneinfo")
            detected = "/".join(parts[idx + 1:])
            ZoneInfo(detected)
            return detected
    except Exception:  # noqa: BLE001
        pass
    return "UTC"


def _tz_offset(tz: str) -> str:
    try:
        offset = datetime.now(ZoneInfo(tz)).utcoffset()
    except Exception:  # noqa: BLE001
        return "+0"
    if offset is None:
        return "+0"
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    return f"{sign}{hours + minutes / 60:g}"


COMMON_TIMEZONES = (
    "Asia/Shanghai",
    "Asia/Hong_Kong",
    "Asia/Taipei",
    "Asia/Tokyo",
    "Asia/Seoul",
    "Asia/Singapore",
    "Asia/Bangkok",
    "Asia/Kolkata",
    "Asia/Dubai",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Europe/Moscow",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Sao_Paulo",
    "Australia/Sydney",
    "Pacific/Auckland",
    "UTC",
)


@lru_cache(maxsize=1)
def _timezone_options() -> dict[str, list[str]]:
    try:
        zones = sorted(available_timezones())
    except Exception:  # noqa: BLE001
        zones = []
    zones = [
        zone for zone in zones
        if zone not in ("localtime", "posixrules")
        and not zone.startswith(("posix/", "right/"))
    ]
    common = [zone for zone in COMMON_TIMEZONES if zone in zones]
    for zone in COMMON_TIMEZONES:
        if zone not in common:
            common.append(zone)
    return {"common": common, "all": zones}


def _ensure_token(name: str, value: str, existing: str) -> str:
    value = (value or "").strip()
    existing = (existing or "").strip()
    if _is_placeholder(value):
        return secrets.token_hex(32)
    if name == "APP_TOKEN":
        if len(value) < 32:
            raise HTTPException(
                status_code=400,
                detail="APP_TOKEN 必须 >=32 字符（openssl rand -hex 32）",
            )
        return value
    # DEVICE_TOKEN：兼容旧固件的短 token；新增/修改必须 >=32
    if len(value) < 32 and value == existing:
        return value
    if len(value) < 32:
        raise HTTPException(
            status_code=400,
            detail="DEVICE_TOKEN 必须 >=32 字符；改 token 要重编固件",
        )
    return value


def _merge_payload(existing: dict[str, str], payload: dict) -> tuple[dict[str, str], Optional[str]]:
    values = payload.get("values") or {}
    secret_values = payload.get("secrets") or {}
    if not isinstance(values, dict) or not isinstance(secret_values, dict):
        raise HTTPException(status_code=400, detail="values/secrets 必须是对象")

    merged = dict(existing)
    for key in VALUE_KEYS:
        if key in values and values[key] is not None:
            merged[key] = str(values[key]).strip()
    for key in SECRET_KEYS:
        if key in secret_values and str(secret_values[key] or "").strip():
            merged[key] = str(secret_values[key]).strip()

    for key, default in DEFAULT_VALUES.items():
        if _is_placeholder(merged.get(key)):
            merged[key] = default

    merged["DEVICE_HOST"] = _validate_host(merged.get("DEVICE_HOST", ""))
    merged["PUBLIC_BASE_URL"] = _validate_base_url(merged.get("PUBLIC_BASE_URL", ""))
    if _is_placeholder(merged.get("TZ")):
        merged["TZ"] = _detect_tz()
    merged["TZ"] = _validate_tz(merged.get("TZ", ""))
    # 只暴露 TZ；偏移始终由时区推导，避免两者不一致
    merged["TZ_OFFSET"] = _tz_offset(merged["TZ"])
    merged["TZ_OFFSET"] = _validate_offset(merged.get("TZ_OFFSET", ""))

    if not (merged.get("LLM_BASE_URL") or "").startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="LLM_BASE_URL 必须以 http(s):// 开头")
    merged["LLM_BASE_URL"] = _llm_base_url(merged.get("LLM_BASE_URL", ""))
    if not merged.get("LLM_MODEL"):
        raise HTTPException(status_code=400, detail="LLM_MODEL 不能为空")
    if not merged.get("TTS_VOICE"):
        raise HTTPException(status_code=400, detail="TTS_VOICE 不能为空")
    if not merged.get("END_PROMPT_TEXT"):
        raise HTTPException(status_code=400, detail="END_PROMPT_TEXT 不能为空")

    llm_key = (merged.get("LLM_API_KEY") or "").strip()
    if _is_placeholder(llm_key):
        raise HTTPException(status_code=400, detail="LLM_API_KEY 未配置")
    merged["APP_TOKEN"] = _ensure_token(
        "APP_TOKEN", merged.get("APP_TOKEN", ""), existing.get("APP_TOKEN", "")
    )
    merged["DEVICE_TOKEN"] = _ensure_token(
        "DEVICE_TOKEN", merged.get("DEVICE_TOKEN", ""), existing.get("DEVICE_TOKEN", "")
    )

    if merged.get("MEMORY") not in ("nomem", "mem_core"):
        raise HTTPException(status_code=400, detail="MEMORY 只能是 nomem 或 mem_core")
    if merged.get("LOG_LEVEL") not in ("INFO", "DEBUG", "WARNING", "ERROR"):
        raise HTTPException(status_code=400, detail="LOG_LEVEL 不合法")
    if merged.get("AUTH_ENABLED") not in ("true", "false"):
        raise HTTPException(status_code=400, detail="AUTH_ENABLED 只能是 true/false")
    if merged.get("COMPOSE_PROFILE") not in ("prod", "dev"):
        raise HTTPException(status_code=400, detail="COMPOSE_PROFILE 只能是 prod/dev")

    merged["PERSONA_FILE"] = (
        merged["PERSONA_FILE"]
        if not _is_placeholder(merged.get("PERSONA_FILE"))
        else "templates/persona.txt"
    )
    # 旧固件的短 token 需要允许 insecure，否则下次用 CLI render 会被拒绝
    short_token = len(merged["APP_TOKEN"]) < 32 or len(merged["DEVICE_TOKEN"]) < 32
    merged["ALLOW_INSECURE_DEFAULTS"] = "1" if short_token else "0"
    merged.setdefault("QWEATHER_API_HOST", "")
    merged.setdefault("QWEATHER_API_KEY", "")
    persona = payload.get("persona")
    if persona is not None:
        persona = str(persona).strip()
        if not persona:
            raise HTTPException(status_code=400, detail="persona 不能为空")
        if len(persona) > 10000:
            raise HTTPException(status_code=400, detail="persona 太长")
    return merged, persona


def _write_env(path: Path, env: dict[str, str]) -> None:
    lines: list[str] = []
    seen: set[str] = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
            if match and match.group(1) in env:
                key = match.group(1)
                lines.append(f"{key}={env[key]}")
                seen.add(key)
            else:
                lines.append(line)
    else:
        lines = ["# StackChan 服务端配置（由 setup 页生成；gitignored）"]
    for key in env:
        if key not in seen:
            lines.append(f"{key}={env[key]}")
    _write_atomic(path, "\n".join(lines) + "\n", 0o600)


def _backup_paths(extra: Optional[list[Path]] = None) -> tuple[Path, dict[Path, Optional[Path]]]:
    tmp = Path(tempfile.mkdtemp(prefix="chan-setup-bak-"))
    files = [DEPLOY_ENV, *(extra or []), SELFHOST / "data" / ".config.yaml",
             SELFHOST / "data" / "agent-base-prompt.txt",
             SELFHOST / "behavior" / ".env", SELFHOST / ".env"]
    backup: dict[Path, Optional[Path]] = {}
    for path in files:
        if path.is_file():
            rel = path.relative_to(SELFHOST)
            dest = tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            backup[path] = dest
        else:
            backup[path] = None
    return tmp, backup


def _restore_paths(backup: dict[Path, Optional[Path]]) -> None:
    for path, saved in backup.items():
        if saved is None:
            if path.is_file():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, path)


def _docker(args: list[str], cwd: Path) -> str:
    if DRY_RUN:
        return "dry-run: " + " ".join(args)
    try:
        proc = subprocess.run(
            ["docker", *args], cwd=str(cwd), capture_output=True, text=True, timeout=240
        )
    except FileNotFoundError:
        raise RuntimeError("docker 命令不存在")
    except subprocess.TimeoutExpired:
        raise RuntimeError("docker 命令超时")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(detail[-1200:] or f"docker 返回 {proc.returncode}")
    return proc.stdout


def _restart_services(profile: str) -> None:
    _docker(
        ["compose", "-f", str(SELFHOST / "docker-compose.yml"),
         "up", "-d", "--force-recreate", "xiaozhi-esp32-server"],
        SELFHOST,
    )
    args = ["compose", "-f", str(SELFHOST / "behavior" / "docker-compose.yml")]
    if profile == "dev":
        args += ["-f", str(SELFHOST / "behavior" / "docker-compose.dev.yml")]
    args += ["up", "-d", "--force-recreate", "chan-behavior"]
    _docker(args, SELFHOST)


def _ota_url(host: str) -> str:
    body = json.dumps({
        "application": {"version": "0.0.0"},
        "board": {"type": "m5stack-stack-chan"},
    }).encode()
    req = urllib.request.Request(
        f"http://{host}:8003/xiaozhi/ota/",
        data=body,
        headers={
            "Device-Id": "setup",
            "Client-Id": "setup",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=4) as resp:
        data = json.loads(resp.read())
    ws = ((data.get("websocket") or {}).get("url") or "").strip()
    if host not in ws:
        raise RuntimeError(f"OTA 返回的 websocket.url 不含 {host}: {ws!r}")
    return ws


def _behavior_health() -> None:
    with urllib.request.urlopen("http://127.0.0.1:8100/health", timeout=3) as resp:
        if resp.status != 200:
            raise RuntimeError(f"behavior /health HTTP {resp.status}")


def _probe(host: str) -> dict:
    if DRY_RUN:
        return {"dry_run": True}
    deadline = time.time() + 60
    last: Optional[Exception] = None
    while time.time() < deadline:
        try:
            ws = _ota_url(host)
            _behavior_health()
            return {"ota": ws, "behavior": "200"}
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2)
    raise RuntimeError(f"健康检查失败（60s）：{last}")


def _containers() -> list[dict[str, str]]:
    if DRY_RUN:
        return []
    try:
        out = _docker(["ps", "-a", "--format", "{{.Names}}|{{.Status}}"], SELFHOST)
    except Exception as exc:  # noqa: BLE001
        return [{"name": "docker", "status": f"不可用：{exc}"}]
    wanted = {"xiaozhi-esp32-server", "chan-behavior"}
    rows = []
    for line in out.splitlines():
        name, _, status = line.partition("|")
        if name in wanted:
            rows.append({"name": name, "status": status})
    return rows


def _logs(service: str) -> str:
    name = "xiaozhi-esp32-server" if service == "xiaozhi" else "chan-behavior"
    if DRY_RUN:
        return "(dry-run：不读取容器日志)"
    try:
        return _docker(["logs", "--tail", "50", name], SELFHOST)
    except Exception as exc:  # noqa: BLE001
        return f"(读取日志失败：{exc})"


def _quick_health() -> dict:
    if DRY_RUN:
        return {"dry_run": True}
    env = _load_env()
    host = env.get("DEVICE_HOST", "")
    result: dict[str, object] = {}
    try:
        result["ota"] = _ota_url(host)
    except Exception as exc:  # noqa: BLE001
        result["ota_error"] = str(exc)[:300]
    try:
        _behavior_health()
        result["behavior"] = "200"
    except Exception as exc:  # noqa: BLE001
        result["behavior_error"] = str(exc)[:300]
    return result


def _http_json(url: str, *, method: str = "GET", headers: Optional[dict] = None,
               payload: Optional[dict] = None, timeout: int = 20) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("Accept-Encoding", "identity")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"连接失败：{exc.reason}")


def _qweather_get(host: str, path: str, params: dict) -> dict:
    base = f"https://{host}" if host else "https://devapi.qweather.com"
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    return _http_json(url, timeout=12)


app = FastAPI(title="chan-setup")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "dry_run": DRY_RUN}


@app.get("/api/session")
async def api_session() -> dict:
    return {"admin_required": REQUIRE_TOKEN, "dry_run": DRY_RUN}


@app.get("/api/config")
async def api_config(_: None = Depends(require_admin)) -> dict:
    env = _load_env()
    values = {key: _clean_value(env, key) for key in VALUE_KEYS}
    values["LLM_BASE_URL"] = _llm_display_url(values.get("LLM_BASE_URL", ""))
    if not values.get("TZ"):
        values["TZ"] = _detect_tz()
    if not values.get("TZ_OFFSET"):
        values["TZ_OFFSET"] = _tz_offset(values["TZ"])
    return {
        "values": values,
        "timezones": _timezone_options(),
        "secrets": {key: _mask(env.get(key)) for key in SECRET_KEYS},
        "secret_hints": SECRET_HINTS,
        "persona": _read_persona(env),
        "firmware": {
            "host": _clean_value(env, "DEVICE_HOST"),
            "token": _clean_value(env, "DEVICE_TOKEN"),
        },
        "pairing": {
            "base": _clean_value(env, "PUBLIC_BASE_URL"),
            "token": _clean_value(env, "APP_TOKEN"),
        },
        "dry_run": DRY_RUN,
        "admin_token_file": str(ADMIN_TOKEN_FILE),
    }


@app.post("/api/config")
async def api_save(payload: dict, _: None = Depends(require_admin)) -> dict:
    existing = _load_env()
    merged, persona = _merge_payload(existing, payload)
    old_profile = existing.get("COMPOSE_PROFILE", "prod")
    persona_path = SELFHOST / (merged.get("PERSONA_FILE") or "templates/persona.txt")
    tmp, backup = _backup_paths([persona_path])
    try:
        _write_env(DEPLOY_ENV, merged)
        if persona is not None:
            _write_atomic(persona_path, persona + ("\n" if persona and not persona.endswith("\n") else ""))
        try:
            files = render.build(merged, allow_insecure=True)
        except SystemExit:
            raise RuntimeError("render 校验失败（看 setup 服务日志）")
        render.write_files(SELFHOST, files)
        if DRY_RUN:
            return {
                "ok": True,
                "dry_run": True,
                "files": sorted(files),
                "message": "dry-run：已写入文件，未重启容器",
            }
        _restart_services(merged["COMPOSE_PROFILE"])
        health = _probe(merged["DEVICE_HOST"])
        return {"ok": True, "dry_run": False, "health": health, "files": sorted(files)}
    except HTTPException:
        _restore_paths(backup)
        raise
    except Exception as exc:  # noqa: BLE001
        _restore_paths(backup)
        rollback_error = None
        if not DRY_RUN:
            try:
                _restart_services(old_profile)
            except Exception as rollback_exc:  # noqa: BLE001
                rollback_error = str(rollback_exc)[:400]
        detail = f"保存失败，已回滚：{exc}"
        if rollback_error:
            detail += f"；回滚重启也失败：{rollback_error}"
        raise HTTPException(status_code=500, detail=detail)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.get("/api/status")
async def api_status(_: None = Depends(require_admin)) -> dict:
    return {
        "dry_run": DRY_RUN,
        "containers": _containers(),
        "health": _quick_health(),
    }


@app.get("/api/logs")
async def api_logs(service: str = "xiaozhi",
                   _: None = Depends(require_admin)) -> dict:
    if service not in ("xiaozhi", "behavior"):
        raise HTTPException(status_code=400, detail="service 只能是 xiaozhi/behavior")
    return {"service": service, "text": _logs(service)}


@app.post("/api/test/llm")
async def api_test_llm(payload: dict, _: None = Depends(require_admin)) -> dict:
    env = _load_env()
    base = (payload.get("base_url") or env.get("LLM_BASE_URL") or "").rstrip("/")
    model = payload.get("model") or env.get("LLM_MODEL") or ""
    key = (payload.get("api_key") or env.get("LLM_API_KEY") or "").strip()
    if not base or not model:
        raise HTTPException(status_code=400, detail="LLM base_url/model 未配置")
    if not key or _is_placeholder(key):
        raise HTTPException(status_code=400, detail="LLM api_key 未配置")
    url = _llm_chat_url(base)
    try:
        data = _http_json(
            url,
            method="POST",
            headers={"Authorization": f"Bearer {key}"},
            payload={
                "model": model,
                "messages": [{"role": "user", "content": "只回复 OK"}],
                "max_tokens": 64,
                "temperature": 0,
            },
        )
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")[:200]
        return {"ok": True, "model": model, "reply": content}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"LLM 测试失败：{exc}")


@app.post("/api/test/weather")
async def api_test_weather(payload: dict,
                           _: None = Depends(require_admin)) -> dict:
    env = _load_env()
    host = (payload.get("api_host") or env.get("QWEATHER_API_HOST") or "").strip()
    key = (payload.get("api_key") or env.get("QWEATHER_API_KEY") or "").strip()
    city = (payload.get("city") or env.get("DEFAULT_CITY") or "北京").strip()
    if not key or _is_placeholder(key):
        raise HTTPException(status_code=400, detail="天气 api_key 未配置")
    try:
        geo = _qweather_get(host, "/geo/v2/city/lookup", {"location": city, "key": key})
        locs = geo.get("location") or []
        if not locs:
            raise RuntimeError(f"找不到城市：{city}")
        loc = locs[0]
        now = _qweather_get(host, "/v7/weather/now",
                            {"location": loc["id"], "key": key})
        return {
            "ok": True,
            "city": f"{loc.get('name')}/{loc.get('adm1')}",
            "text": (now.get("now") or {}).get("text"),
            "temp": (now.get("now") or {}).get("temp"),
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"天气测试失败：{exc}")


@app.post("/api/test/ota")
async def api_test_ota(payload: dict,
                       _: None = Depends(require_admin)) -> dict:
    env = _load_env()
    host = (payload.get("host") or env.get("DEVICE_HOST") or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail="DEVICE_HOST 未配置")
    try:
        return {"ok": True, "websocket": _ota_url(host)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"OTA 测试失败：{exc}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host=HOST, port=PORT, app_dir=str(APP_DIR))
