"""把旧的 data/.config.yaml + behavior/.env 迁移成 deploy.env。

用法：
  python3 selfhost/setup/migrate.py                 # 只打印摘要（key 掩码）
  python3 selfhost/setup/migrate.py --write         # 写 deploy.env + persona
  python3 selfhost/setup/migrate.py --profile dev --write
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SELFHOST = Path(os.environ.get("CHAN_SELFHOST_DIR", ROOT / "selfhost"))
OLD_CONFIG = SELFHOST / "data" / ".config.yaml"
BEHAVIOR_ENV = SELFHOST / "behavior" / ".env"
DEPLOY_ENV = SELFHOST / "deploy.env"
PERSONA_TEMPLATE = SELFHOST / "templates" / "persona.txt"
PERSONA_MIGRATED = SELFHOST / "templates" / "persona.txt.migrated"

SECRET_KEYS = {"LLM_API_KEY", "QWEATHER_API_KEY", "APP_TOKEN", "DEVICE_TOKEN"}


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _host_from_url(url: object) -> str:
    if not url:
        return ""
    match = re.match(r"^[a-z]+://([^/:]+)", str(url).strip())
    return match.group(1) if match else ""


def _mask(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return "(未配置)"
    if len(value) <= 8:
        return "…" + value[-2:]
    return value[:3] + "…" + value[-4:]


def from_existing(selfhost: Path | None = None) -> dict[str, str]:
    """只读地推断 deploy.env；找不到可迁移配置时返回 {}。"""
    sh = selfhost or SELFHOST
    old = sh / "data" / ".config.yaml"
    if not old.is_file():
        return {}
    try:
        cfg = yaml.safe_load(old.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}

    benv = _parse_env(sh / "behavior" / ".env")
    server = cfg.get("server") or {}
    selected = cfg.get("selected_module") or {}
    host = _host_from_url(server.get("websocket")) or _host_from_url(
        server.get("vision_explain")
    )
    if not host:
        return {}

    llm_name = selected.get("LLM") or "DeepSeekLLM"
    llm = ((cfg.get("LLM") or {}).get(llm_name) or {})
    tts_name = selected.get("TTS") or "EdgeTTS"
    tts = ((cfg.get("TTS") or {}).get(tts_name) or {})
    weather = ((cfg.get("plugins") or {}).get("get_weather") or {})
    log = cfg.get("log") or {}
    end = cfg.get("end_prompt") or {}
    auth = server.get("auth") or {}
    allowed = auth.get("allowed_devices") or []
    if isinstance(allowed, list):
        allowed = ",".join(str(item) for item in allowed)

    raw_offset = server.get("timezone_offset", "+8")
    if isinstance(raw_offset, (int, float)):
        offset = f"{raw_offset:+g}"
    else:
        offset = str(raw_offset).strip()
        if offset and offset[0] not in "+-":
            offset = "+" + offset
    tz = "Asia/Shanghai" if offset in ("+8", "8") else "UTC"
    public = benv.get("PUBLIC_BASE_URL") or f"http://{host}:8100"
    device_token = benv.get("DEVICE_TOKEN", "")
    allow_insecure = "1" if 0 < len(device_token) < 32 else "0"

    return {
        "DEVICE_HOST": host,
        "PUBLIC_BASE_URL": public.rstrip("/"),
        "LLM_API_KEY": str(llm.get("api_key") or ""),
        "LLM_MODEL": str(llm.get("model_name") or "deepseek-flash"),
        "LLM_BASE_URL": str(
            llm.get("base_url") or llm.get("url") or "https://api.deepseek.com/v1"
        ),
        "LLM_TEMPERATURE": str(llm.get("temperature") or "0.7"),
        "LLM_MAX_TOKENS": str(llm.get("max_tokens") or "500"),
        "QWEATHER_API_HOST": str(weather.get("api_host") or ""),
        "QWEATHER_API_KEY": str(weather.get("api_key") or ""),
        "DEFAULT_CITY": str(weather.get("default_location") or "北京"),
        "TTS_VOICE": str(
            tts.get("voice") or benv.get("TTS_VOICE") or "zh-CN-XiaoyiNeural"
        ),
        "TZ": tz,
        "TZ_OFFSET": offset,
        "APP_TOKEN": benv.get("APP_TOKEN", ""),
        "DEVICE_TOKEN": device_token,
        "MEMORY": str(selected.get("Memory") or "nomem"),
        "LOG_LEVEL": str(log.get("log_level") or "INFO"),
        "AUTH_ENABLED": "true" if auth.get("enabled") else "false",
        "ALLOWED_DEVICES": str(allowed),
        "END_PROMPT_TEXT": str(end.get("text") or "我先去待机啦，下次聊"),
        "ALLOW_INSECURE_DEFAULTS": allow_insecure,
        "PERSONA_FILE": "templates/persona.txt",
        "COMPOSE_PROFILE": "prod",
    }


def write_deploy(
    selfhost: Path | None = None, *, profile: str = "prod", write_persona: bool = True
) -> dict[str, str]:
    sh = selfhost or SELFHOST
    env = from_existing(sh)
    if not env:
        raise SystemExit("找不到可迁移的 data/.config.yaml（或解析失败）")
    env["COMPOSE_PROFILE"] = profile

    old = sh / "data" / ".config.yaml"
    try:
        cfg = yaml.safe_load(old.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        cfg = {}
    prompt = str(cfg.get("prompt") or "").strip()
    if write_persona and prompt:
        migrated = sh / "templates" / "persona.txt.migrated"
        migrated.parent.mkdir(parents=True, exist_ok=True)
        migrated.write_text(prompt + "\n", encoding="utf-8")
        os.chmod(migrated, 0o600)
        env["PERSONA_FILE"] = "templates/persona.txt.migrated"

    target = sh / "deploy.env"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if target.exists():
        backup = target.with_name(target.name + f".bak-{stamp}")
        shutil.copy2(target, backup)
        print(f"backup  {backup}")

    lines = [
        "# 由 setup/migrate.py 从 data/.config.yaml + behavior/.env 生成",
        f"# {stamp}",
    ]
    for key, value in env.items():
        lines.append(f"{key}={value}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(target, 0o600)
    print(f"write   {target}")
    if env.get("PERSONA_FILE", "").endswith(".migrated"):
        print(f"write   {sh / env['PERSONA_FILE']}")
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="写 deploy.env")
    parser.add_argument("--profile", default="prod", choices=["prod", "dev"])
    parser.add_argument("--selfhost", type=Path, default=SELFHOST)
    args = parser.parse_args()

    env = from_existing(args.selfhost)
    if not env:
        print("找不到可迁移的 data/.config.yaml（或解析失败）", file=sys.stderr)
        return 1

    if env.get("LLM_MODEL") == "deepseek-chat":
        print(
            "warning: deepseek-chat 是旧兼容别名；当前官方模型是 "
            "deepseek-flash / deepseek-v4-pro，建议在配置页改成 deepseek-flash。",
            file=sys.stderr,
        )

    print("迁移摘要（key 已掩码）：")
    for key, value in env.items():
        if key in SECRET_KEYS:
            value = _mask(value)
        print(f"  {key}={value}")

    if not args.write:
        print("\n只预览；确认后加 --write")
        return 0

    write_deploy(args.selfhost, profile=args.profile)
    print("\n下一步：打开配置页核对，再「保存并重启」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
