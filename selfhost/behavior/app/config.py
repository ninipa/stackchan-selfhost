import os
import warnings
from dataclasses import dataclass


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _allow_insecure() -> bool:
    return os.environ.get("ALLOW_INSECURE_DEFAULTS", "0").strip() == "1"


def _validate_app_token(value: str) -> None:
    if len(value) >= 32:
        return
    if not _allow_insecure():
        raise RuntimeError(
            "APP_TOKEN 长度 <32。用 `openssl rand -hex 32` 生成；"
            "隔离的开发环境可设 ALLOW_INSECURE_DEFAULTS=1。"
        )
    warnings.warn("APP_TOKEN 长度 <32，仅适用于隔离的开发环境", RuntimeWarning)


def _validate_device_token(value: str) -> None:
    # 2026-11-13：暂时只警告。现网 DEVICE_TOKEN 还是 16 字符，硬失败会让服务起不来；
    # 阶段 2 轮换 DEVICE_TOKEN 后改成和 APP_TOKEN 一样硬失败。
    if len(value) < 32:
        warnings.warn("DEVICE_TOKEN 长度 <32；阶段 2 轮换后会要求 >=32", RuntimeWarning)


@dataclass(frozen=True)
class Settings:
    public_base_url: str
    device_token: str
    app_token: str
    tts_voice: str
    data_dir: str
    default_gap_ms: int
    max_repeat: int = 20
    device_hello_timeout: float = 10.0
    # xiaozhi 的配置文件，只读挂载进来给 App 展示"主对话音色"这类改不动的值。
    # 挂不到就当没配，接口返回 None，不影响其他功能。
    xiaozhi_config: str = "/xiaozhi-config/.config.yaml"
    # 历史保留条数：只在点"清理"时生效，不做后台定时清理。
    history_keep: int = 200
    # 备忘录：循环规则的本地时区 + tick 间隔/离线补发宽限。
    timezone: str = "Asia/Shanghai"
    memo_tick_seconds: int = 15
    memo_grace_seconds: int = 600


def load_settings() -> Settings:
    app_token = os.environ.get("APP_TOKEN", "dev-token")
    device_token = os.environ.get("DEVICE_TOKEN", "dev-device-token")
    _validate_app_token(app_token)
    _validate_device_token(device_token)
    return Settings(
        public_base_url=os.environ.get("PUBLIC_BASE_URL", "http://localhost:8100").rstrip("/"),
        device_token=device_token,
        app_token=app_token,
        # 与 selfhost/data/.config.yaml 的 TTS.EdgeTTS.voice 保持一致（2026-11-09 统一为晓伊）
        tts_voice=os.environ.get("TTS_VOICE", "zh-CN-XiaoyiNeural"),
        data_dir=os.environ.get("DATA_DIR", "data"),
        default_gap_ms=_get_int("DEFAULT_GAP_MS", 800),
        max_repeat=_get_int("MAX_REPEAT", 20),
        xiaozhi_config=os.environ.get("XIAOZHI_CONFIG", "/xiaozhi-config/.config.yaml"),
        history_keep=_get_int("HISTORY_KEEP", 200),
        timezone=os.environ.get("TZ", "Asia/Shanghai") or "Asia/Shanghai",
        memo_tick_seconds=_get_int("MEMO_TICK_SECONDS", 15),
        memo_grace_seconds=_get_int("MEMO_GRACE_SECONDS", 600),
    )


settings = load_settings()
