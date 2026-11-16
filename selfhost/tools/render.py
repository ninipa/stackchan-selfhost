#!/usr/bin/env python3
"""Render StackChan server config from deploy.env + templates.

用法：
  ./selfhost/tools/render-config.sh --check            # 只比较，不写（默认）
  ./selfhost/tools/render-config.sh --write            # 写入，先备份
  ./selfhost/tools/render-config.sh --print            # 打印渲染结果
  ./selfhost/tools/render-config.sh --firmware-snippet # 打印固件 local.conf 片段

生成：
  selfhost/data/.config.yaml
  selfhost/data/agent-base-prompt.txt
  selfhost/behavior/.env
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[2]
SELFHOST = ROOT / "selfhost"
TEMPLATES = SELFHOST / "templates"
DEFAULT_ENV = SELFHOST / "deploy.env"

PLACEHOLDER = "__REPLACE_"
TZ_OFFSET_RE = re.compile(r"[+-]\d{1,2}(?:\.\d{1,2})?")
HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]*")
TEMPLATE_VAR_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def parse_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        die(f"找不到 {path}（先 cp deploy.env.example deploy.env）")
    values: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            die(f"{path}:{lineno} 不是 KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key:
            die(f"{path}:{lineno} 空 key")
        values[key] = value
    return values


def require(env: dict[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value or value.startswith(PLACEHOLDER):
        die(f"{key} 未填写（见 deploy.env.example）")
    return value


def optional(env: dict[str, str], key: str, default: str = "") -> str:
    value = env.get(key, default).strip()
    if not value or value.startswith(PLACEHOLDER):
        return default
    return value


def validate_host(value: str) -> str:
    if "://" in value or "/" in value:
        die(f"DEVICE_HOST 不能带 http:// 或路径：{value}")
    if not HOST_RE.fullmatch(value):
        die(f"DEVICE_HOST 只支持 IP 或主机名：{value}")
    return value


def validate_base_url(value: str) -> str:
    if not value.startswith(("http://", "https://")):
        die(f"PUBLIC_BASE_URL 必须以 http:// 或 https:// 开头：{value}")
    return value.rstrip("/")


def validate_offset(value: str) -> str:
    if not TZ_OFFSET_RE.fullmatch(value):
        die(f"TZ_OFFSET 格式应为 +8 / -5 / +5.5：{value}")
    return value


def validate_tz(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError:
        die(f"TZ 不是合法时区（例：Asia/Shanghai）：{value}")
    return value


def detect_tz() -> str:
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


def detect_tz_offset(tz: str) -> str:
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


def validate_token(name: str, value: str, allow_insecure: bool) -> str:
    if len(value) < 32:
        if not allow_insecure:
            die(
                f"{name} 长度 <32；用 `openssl rand -hex 32` 生成，"
                "或设 ALLOW_INSECURE_DEFAULTS=1（仅隔离开发环境）"
            )
        warn(f"{name} 长度 <32，仅适用于隔离的开发环境")
    return value


def indent_block(text: str, spaces: int = 2) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else "" for line in text.splitlines())


def allowed_devices_yaml(raw: str) -> str:
    items = [item.strip() for item in raw.split(",") if item.strip()]
    if not items:
        return "[]"
    return "\n" + "\n".join(f'      - "{item}"' for item in items)


def substitute(template: str, values: dict[str, str], name: str) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            die(f"{name}: 模板里的 ${{{key}}} 没有对应值")
        return values[key]

    return TEMPLATE_VAR_RE.sub(repl, template)


def build(env: dict[str, str], *, allow_insecure: bool) -> dict[str, str]:
    host = validate_host(require(env, "DEVICE_HOST"))
    public_base = validate_base_url(require(env, "PUBLIC_BASE_URL"))

    llm_key = require(env, "LLM_API_KEY")
    llm_model = require(env, "LLM_MODEL")
    llm_base = require(env, "LLM_BASE_URL")
    if not llm_base.startswith(("http://", "https://")):
        die(f"LLM_BASE_URL 必须以 http:// 或 https:// 开头：{llm_base}")
    llm_temp = optional(env, "LLM_TEMPERATURE", "0.7")
    llm_max = optional(env, "LLM_MAX_TOKENS", "500")

    qweather_host = optional(env, "QWEATHER_API_HOST")
    qweather_key = optional(env, "QWEATHER_API_KEY")
    if not qweather_key:
        warn("QWEATHER_API_KEY 为空：天气插件会返回不可用")
    city = optional(env, "DEFAULT_CITY", "北京")

    voice = require(env, "TTS_VOICE")
    tz = validate_tz(optional(env, "TZ") or detect_tz())
    tz_offset = validate_offset(optional(env, "TZ_OFFSET") or detect_tz_offset(tz))

    allow = allow_insecure or env.get("ALLOW_INSECURE_DEFAULTS", "0").strip() == "1"
    app_token = validate_token("APP_TOKEN", require(env, "APP_TOKEN"), allow)
    device_token = validate_token("DEVICE_TOKEN", require(env, "DEVICE_TOKEN"), allow)

    memory = optional(env, "MEMORY", "nomem")
    if memory not in ("nomem", "mem_core"):
        die("MEMORY 只能是 nomem 或 mem_core")

    auth = optional(env, "AUTH_ENABLED", "false").lower()
    if auth not in ("true", "false"):
        die("AUTH_ENABLED 只能是 true 或 false")

    log_level = optional(env, "LOG_LEVEL", "INFO")
    end_text = optional(env, "END_PROMPT_TEXT", "我先去待机啦，下次聊").replace('"', "'")

    persona_path = Path(optional(env, "PERSONA_FILE", "templates/persona.txt"))
    if not persona_path.is_absolute():
        persona_path = SELFHOST / persona_path
    if not persona_path.is_file():
        die(f"找不到人设文件：{persona_path}")
    persona = indent_block(persona_path.read_text(encoding="utf-8").rstrip("\n"))

    agent_prompt = TEMPLATES / "agent-base-prompt.txt"
    if not agent_prompt.is_file():
        die(f"找不到 {agent_prompt}")

    behavior_internal_url = validate_base_url(
        optional(env, "BEHAVIOR_INTERNAL_URL", "http://host.docker.internal:8100")
    )

    values = {
        "DEVICE_HOST": host,
        "PUBLIC_BASE_URL": public_base,
        "BEHAVIOR_INTERNAL_URL": behavior_internal_url,
        "LLM_API_KEY": llm_key,
        "LLM_MODEL": llm_model,
        "LLM_BASE_URL": llm_base,
        "LLM_TEMPERATURE": llm_temp,
        "LLM_MAX_TOKENS": llm_max,
        "QWEATHER_API_HOST": qweather_host,
        "QWEATHER_API_KEY": qweather_key,
        "DEFAULT_CITY": city,
        "TTS_VOICE": voice,
        "TZ": tz,
        "TZ_OFFSET": tz_offset,
        "APP_TOKEN": app_token,
        "DEVICE_TOKEN": device_token,
        "MEMORY": memory,
        "LOG_LEVEL": log_level,
        "AUTH_ENABLED": auth,
        "ALLOWED_DEVICES_YAML": allowed_devices_yaml(optional(env, "ALLOWED_DEVICES")),
        "END_PROMPT_TEXT": end_text,
        "PERSONA_INDENTED": persona,
        "BEHAVIOR_BASE_IMAGE": optional(env, "BEHAVIOR_BASE_IMAGE"),
        "APT_MIRROR": optional(env, "APT_MIRROR"),
        "PIP_INDEX_URL": optional(env, "PIP_INDEX_URL"),
        "ALLOW_INSECURE_DEFAULTS": "1" if allow else "0",
    }

    config = substitute(
        (TEMPLATES / "config.yaml.template").read_text(encoding="utf-8"),
        values,
        "config.yaml.template",
    )
    if memory == "mem_core":
        config = (
            config.rstrip()
            + "\n\n"
            + (TEMPLATES / "memory.mem_core.template").read_text(encoding="utf-8").rstrip()
            + "\n"
        )

    behavior_env = substitute(
        (TEMPLATES / "behavior.env.template").read_text(encoding="utf-8"),
        values,
        "behavior.env.template",
    )

    # Compose 在 selfhost/ 下自动读这个文件；只放替换用变量，不放密钥。
    compose_lines = [f"TZ={tz}"]
    for key in ("XIAOZHI_IMAGE", "BEHAVIOR_BASE_IMAGE", "APT_MIRROR", "PIP_INDEX_URL"):
        value = optional(env, key)
        if value:
            compose_lines.append(f"{key}={value}")
    compose_env = "\n".join(compose_lines) + "\n"

    return {
        "data/.config.yaml": config,
        "data/agent-base-prompt.txt": agent_prompt.read_text(encoding="utf-8"),
        "behavior/.env": behavior_env,
        ".env": compose_env,
    }


def write_files(target: Path, files: dict[str, str]) -> None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for rel, content in files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup = path.with_name(path.name + f".bak-{stamp}")
            shutil.copy2(path, backup)
            print(f"backup  {backup}")
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        print(f"write   {path}")


def check_files(target: Path, files: dict[str, str]) -> int:
    rc = 0
    for rel, content in files.items():
        path = target / rel
        if not path.is_file():
            print(f"missing {path}")
            rc = 1
            continue
        old = path.read_text(encoding="utf-8")
        if old == content:
            print(f"ok      {path}")
            continue
        rc = 1
        print(f"diff    {path}")
        diff = difflib.unified_diff(
            old.splitlines(),
            content.splitlines(),
            fromfile=str(path),
            tofile="rendered",
            lineterm="",
        )
        for line in list(diff)[:80]:
            print(line)
    return rc


def print_files(files: dict[str, str]) -> None:
    for rel, content in files.items():
        print(f"===== {rel} =====")
        print(content, end="" if content.endswith("\n") else "\n")


def firmware_snippet(env: dict[str, str]) -> None:
    host = validate_host(require(env, "DEVICE_HOST"))
    device_token = require(env, "DEVICE_TOKEN")
    print("# 阶段 2 之后的新固件：只需要这两个 + TX buffer")
    print(f'CONFIG_CHAN_SERVER_HOST="{host}"')
    print(f'CONFIG_CHAN_DEVICE_TOKEN="{device_token}"')
    print("CONFIG_ESP_WIFI_STATIC_TX_BUFFER_NUM=8")
    print()
    print("# 阶段 2 之前的旧固件：用这三行")
    print(f'CONFIG_OTA_URL="http://{host}:8003/xiaozhi/ota/"')
    print(f'CONFIG_CHAN_BEHAVIOR_WS_URL="ws://{host}:8100/device/ws?token={device_token}"')
    print(f'CONFIG_CHAN_TELEOP_VIDEO_WS_URL="ws://{host}:8100/device/video?token={device_token}"')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument(
        "--target",
        type=Path,
        default=SELFHOST,
        help="输出根目录（默认 selfhost/）",
    )
    parser.add_argument("--write", action="store_true", help="写入（先备份）")
    parser.add_argument("--check", action="store_true", help="只比较，不写（默认）")
    parser.add_argument("--print", dest="print_rendered", action="store_true", help="打印渲染结果")
    parser.add_argument("--firmware-snippet", action="store_true", help="打印固件 local.conf 片段")
    parser.add_argument("--allow-insecure", action="store_true", help="允许 <32 字符的 token（仅开发）")
    args = parser.parse_args()

    env = parse_env(args.env_file)
    allow_insecure = args.allow_insecure or env.get("ALLOW_INSECURE_DEFAULTS", "0").strip() == "1"

    if args.firmware_snippet:
        firmware_snippet(env)
        return 0

    files = build(env, allow_insecure=allow_insecure)

    if args.print_rendered:
        print_files(files)
        return 0
    if args.write:
        write_files(args.target.resolve(), files)
        return 0
    return check_files(args.target.resolve(), files)


if __name__ == "__main__":
    raise SystemExit(main())
