"""番茄钟语音设置（服务端 function）。

链路：LLM -> start_pomodoro -> behavior `POST /api/timers` -> 设备 `timer_start`。

为什么不是设备 MCP：
- 番茄钟的权威状态在 behavior（SQLite jobs 表），设备只负责显示和播报；
- 服务端函数直接调 behavior，避免设备当代理，也避免两套计时状态。

槽位：
- duration_minutes：必需；缺失时由函数直接返回一句澄清（Action.RESPONSE，不再绕 LLM）。
- task：可选的任务名（App 的「这次要做什么」，max 20），不是固定标签「专注」；
  label 固定传「专注」，和 App 的 `label: '专注'` 一致。
- 用户上一轮只说了任务名时，用 conn 上的 pending draft 合并；2 分钟过期。

挂载方式：`selfhost/patches/functions/pomodoro.py` →
`plugins_func/functions/pomodoro.py`（只读）。插件目录是扫描发现的，
新增文件即自动注册；还要在 `Intent.function_call.functions` 里列出 `start_pomodoro`。
"""
from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

import httpx

from config.logger import setup_logging
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

PENDING_TTL_SECONDS = 120
TASK_MAX_LEN = 20
MIN_MINUTES = 1
MAX_MINUTES = 240

START_POMODORO_DESC = {
    "type": "function",
    "function": {
        "name": "start_pomodoro",
        "description": (
            "开始一次番茄钟（倒计时专注）。用户说“番茄钟/专注计时/开始专注”时调用。"
            "duration_minutes 是分钟数（1-240）；用户没说就省略或传 null，不要猜。"
            "task 是任务名（例如“写周报”），不是固定标签“专注”；用户没说就省略或传 null。"
            "缺时长时函数会返回一句追问，你直接照说即可。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "duration_minutes": {
                    "type": "integer",
                    "description": "专注时长（分钟），1-240；没说就省略",
                },
                "task": {
                    "type": "string",
                    "description": "任务名，最多 20 字；没说就省略",
                },
            },
        },
    },
}


STOP_POMODORO_DESC = {
    "type": "function",
    "function": {
        "name": "stop_pomodoro",
        "description": (
            "结束当前设备上正在运行的番茄钟。用户说“关闭/结束/停止番茄钟”时调用。"
            "默认不要传 action：函数按当前阶段决定——已到点按完成（on_time/late），"
            "还在倒计时按放弃。只有用户明确说“完成/我完成了”才传 finish；"
            "明确说“放弃/不做了”才传 cancel。"
            "函数会先查当前状态；没有在跑的番茄钟会如实说明，不要凭空回答。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["finish", "cancel"],
                    "description": "finish=完成（默认）；cancel=放弃",
                },
            },
        },
    },
}


def _plugin_config(conn: "ConnectionHandler") -> dict[str, Any]:
    plugins = conn.config.get("plugins", {}) or {}
    return plugins.get("pomodoro", {}) or {}


def _device_id(conn: "ConnectionHandler") -> str:
    """上报给 behavior 的设备 ID 是 MAC 去掉冒号（例如 aabbccddeeff）。"""
    raw = str(getattr(conn, "device_id", "") or "")
    return raw.replace(":", "").replace("-", "").strip().lower()


def _pending(conn: "ConnectionHandler") -> dict[str, Any]:
    pending = getattr(conn, "_pomodoro_pending", None)
    if not isinstance(pending, dict):
        return {}
    if time.time() - float(pending.get("ts", 0)) > PENDING_TTL_SECONDS:
        return {}
    return pending


def _remember(conn: "ConnectionHandler", *, task: str = "") -> None:
    setattr(
        conn,
        "_pomodoro_pending",
        {"task": task, "ts": time.time()},
    )


def _forget(conn: "ConnectionHandler") -> None:
    if hasattr(conn, "_pomodoro_pending"):
        delattr(conn, "_pomodoro_pending")


def _parse_minutes(value: Any) -> tuple[int | None, str | None]:
    """返回 (minutes, error)。error 为 missing/invalid；valid 时两者都是 None。"""
    if value is None or value == "":
        return None, "missing"
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None, "invalid"
    if isinstance(value, float) and value != minutes:
        return None, "invalid"
    if minutes < MIN_MINUTES or minutes > MAX_MINUTES:
        return None, "invalid"
    return minutes, None


def _normalize_task(value: Any) -> str:
    return str(value or "").strip()[:TASK_MAX_LEN]


# 判断用户这句话里有没有“时长信息”。只做「有没有」的判断，具体数值交给 LLM 抽取。
# 目的：模型经常在用户没说时长时自己猜 25 分钟；没有这个守卫就会直接开跑。
_TIME_HINT_RE = re.compile(
    r"(分钟|小时|钟头|一刻钟|min|minute|hour|h\b|\d+\s*分|[零一二三四五六七八九十百两]+\s*分)",
    re.IGNORECASE,
)


def _last_user_text(conn: "ConnectionHandler") -> str:
    """读取本轮用户原话（process_function_call 在调用函数前已写入 dialogue）。"""
    dialogue = getattr(conn, "dialogue", None)
    messages = getattr(dialogue, "dialogue", None) or []
    for message in reversed(messages):
        if getattr(message, "role", "") == "user":
            return str(getattr(message, "content", "") or "")
    return ""


def _user_mentioned_duration(text: str) -> bool | None:
    """None = 拿不到原话，无法判断；True/False = 有没有时长线索。"""
    if not text:
        return None
    return bool(_TIME_HINT_RE.search(text))


async def _post_timer(
    base_url: str,
    app_token: str,
    timeout: float,
    *,
    device_id: str,
    minutes: int,
    task: str,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=2.0)) as client:
        return await client.post(
            f"{base_url}/api/timers",
            json={
                "duration_ms": minutes * 60_000,
                "label": "专注",
                "task": task,
                "device_id": device_id,
            },
            headers={"X-App-Token": app_token},
        )


async def _get_active_timer(
    base_url: str,
    app_token: str,
    timeout: float,
    *,
    device_id: str,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=2.0)) as client:
        return await client.get(
            f"{base_url}/api/timers/active",
            params={"device_id": device_id},
            headers={"X-App-Token": app_token},
        )


async def _post_timer_action(
    base_url: str,
    app_token: str,
    timeout: float,
    *,
    job_id: str,
    action: str,
    announce: bool,
) -> httpx.Response:
    """finish=完成（计入 outcome）；cancel=放弃。语音链路用 announce=false 自己回话。"""
    params = {"announce": "true" if announce else "false"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=2.0)) as client:
        return await client.post(
            f"{base_url}/api/jobs/{job_id}/{action}",
            params=params,
            headers={"X-App-Token": app_token},
        )


@register_function("start_pomodoro", START_POMODORO_DESC, ToolType.SYSTEM_CTL)
async def start_pomodoro(
    conn: "ConnectionHandler",
    duration_minutes=None,
    task=None,
):
    cfg = _plugin_config(conn)
    base_url = str(cfg.get("base_url") or "").rstrip("/")
    app_token = str(cfg.get("app_token") or "")
    timeout = float(cfg.get("timeout", 5) or 5)

    # 上一轮只说了任务名时，把任务名接上。
    user_text = _last_user_text(conn)
    pending = _pending(conn)
    task_text = _normalize_task(task) if task is not None else ""
    if not task_text and pending.get("task"):
        task_text = str(pending["task"])
    # 模型偶尔把固定标签「专注」当任务名传进来；用户原话没说过就丢掉。
    if task_text == "专注" and "专注" not in user_text:
        task_text = ""

    minutes, error = _parse_minutes(duration_minutes)
    if minutes is not None:
        mentioned = _user_mentioned_duration(user_text)
        if mentioned is False:
            logger.bind(tag=TAG).info(
                f"start_pomodoro 忽略模型猜的时长 | device={_device_id(conn)} | "
                f"guessed={minutes} | user_text={user_text!r}"
            )
            minutes = None
            error = "missing"
    if minutes is None:
        _remember(conn, task=task_text)
        if error == "invalid":
            return ActionResponse(
                Action.RESPONSE,
                response="专注时长要在 1 到 240 分钟之间。要专注多久？",
            )
        prefix = f"{task_text}，" if task_text else ""
        return ActionResponse(
            Action.RESPONSE,
            response=f"{prefix}要专注多久？15、25、45 分钟，还是自定义？",
        )

    if not base_url or not app_token:
        logger.bind(tag=TAG).error("start_pomodoro 未配置 base_url/app_token")
        return ActionResponse(
            Action.RESPONSE,
            response="番茄钟还没有配置好，先在配置页填一下服务地址和 token。",
        )

    device_id = _device_id(conn)
    if not device_id:
        logger.bind(tag=TAG).error("start_pomodoro 缺少 device_id")
        return ActionResponse(Action.RESPONSE, response="没拿到设备 ID，稍后再试。")

    logger.bind(tag=TAG).info(
        f"start_pomodoro | device={device_id} | minutes={minutes} | "
        f"task={task_text!r} | pending={bool(pending)}"
    )

    try:
        resp = await _post_timer(
            base_url,
            app_token,
            timeout,
            device_id=device_id,
            minutes=minutes,
            task=task_text,
        )
    except httpx.TimeoutException:
        logger.bind(tag=TAG).error(f"start_pomodoro 超时 | device={device_id}")
        return ActionResponse(Action.RESPONSE, response="定时服务没响应，稍后再试。")
    except Exception as exc:  # noqa: BLE001
        logger.bind(tag=TAG).error(f"start_pomodoro 失败 | device={device_id} | {exc}")
        return ActionResponse(
            Action.RESPONSE,
            response="番茄钟没设上，定时服务可能不在线。",
        )

    if resp.status_code == 200:
        _forget(conn)
        # 番茄钟已经开始，这一轮语音到此为止：说完确认语就把会话收掉，
        # 设备回 idle，行为层的 HUD 才能显示出来（pomodoro_update 在
        # xiaozhi 非 idle 时会主动隐藏 HUD）。
        conn.close_after_chat = True
        logger.bind(tag=TAG).info("start_pomodoro 已开始，确认语后关闭本次会话")
        tail = f"，{task_text}" if task_text else ""
        return ActionResponse(
            Action.RESPONSE,
            response=f"好，{minutes} 分钟{tail}，开始。",
        )

    detail = ""
    try:
        detail = str(resp.json().get("detail", ""))
    except Exception:  # noqa: BLE001
        detail = ""
    _forget(conn)
    logger.bind(tag=TAG).warning(
        f"start_pomodoro HTTP {resp.status_code} | device={device_id} | detail={detail!r}"
    )
    if resp.status_code == 409 and "already running" in detail:
        return ActionResponse(
            Action.RESPONSE,
            response="已经有一个番茄钟在跑，先在手机上取消或等它结束。",
        )
    if resp.status_code == 409 and "offline" in detail:
        return ActionResponse(
            Action.RESPONSE,
            response="设备现在不在线，等它连上再试。",
        )
    return ActionResponse(Action.RESPONSE, response="番茄钟没设上，稍后再试。")


@register_function("stop_pomodoro", STOP_POMODORO_DESC, ToolType.SYSTEM_CTL)
async def stop_pomodoro(conn: "ConnectionHandler", action=None):
    cfg = _plugin_config(conn)
    base_url = str(cfg.get("base_url") or "").rstrip("/")
    app_token = str(cfg.get("app_token") or "")
    timeout = float(cfg.get("timeout", 5) or 5)

    # 用户改主意不开始了：把上一轮的“要专注多久”草稿清掉。
    _forget(conn)

    if not base_url or not app_token:
        logger.bind(tag=TAG).error("stop_pomodoro 未配置 base_url/app_token")
        return ActionResponse(
            Action.RESPONSE,
            response="番茄钟还没有配置好，先在配置页填一下服务地址和 token。",
        )

    device_id = _device_id(conn)
    if not device_id:
        logger.bind(tag=TAG).error("stop_pomodoro 缺少 device_id")
        return ActionResponse(Action.RESPONSE, response="没拿到设备 ID，稍后再试。")

    try:
        active_resp = await _get_active_timer(
            base_url, app_token, timeout, device_id=device_id
        )
    except httpx.TimeoutException:
        logger.bind(tag=TAG).error(f"stop_pomodoro 查询超时 | device={device_id}")
        return ActionResponse(Action.RESPONSE, response="定时服务没响应，稍后再试。")
    except Exception as exc:  # noqa: BLE001
        logger.bind(tag=TAG).error(
            f"stop_pomodoro 查询失败 | device={device_id} | {exc}"
        )
        return ActionResponse(
            Action.RESPONSE,
            response="番茄钟状态没查到，定时服务可能不在线。",
        )

    if active_resp.status_code != 200:
        logger.bind(tag=TAG).warning(
            f"stop_pomodoro active HTTP {active_resp.status_code} | device={device_id}"
        )
        return ActionResponse(Action.RESPONSE, response="番茄钟状态没查到，稍后再试。")

    try:
        timer = (active_resp.json() or {}).get("timer")
    except Exception:  # noqa: BLE001
        timer = None
    if not isinstance(timer, dict) or not timer.get("job_id"):
        logger.bind(tag=TAG).info(f"stop_pomodoro: no active timer | device={device_id}")
        return ActionResponse(Action.RESPONSE, response="现在没有在跑的番茄钟。")

    job_id = str(timer["job_id"])
    phase = str(timer.get("phase") or "")
    requested = str(action or "").strip().lower()
    if requested == "cancel":
        mode = "cancel"
    elif requested == "finish":
        mode = "finish"
    else:
        # 默认语义：到点=完成；还在倒计时=放弃。避免一句“关闭”被算成提前完成。
        mode = "finish" if phase == "overtime" else "cancel"

    try:
        resp = await _post_timer_action(
            base_url,
            app_token,
            timeout,
            job_id=job_id,
            action=mode,
            announce=False,
        )
    except httpx.TimeoutException:
        logger.bind(tag=TAG).error(
            f"stop_pomodoro {mode} 超时 | device={device_id} | job={job_id}"
        )
        return ActionResponse(Action.RESPONSE, response="关闭番茄钟超时了，稍后再试。")
    except Exception as exc:  # noqa: BLE001
        logger.bind(tag=TAG).error(
            f"stop_pomodoro {mode} 失败 | device={device_id} | job={job_id} | {exc}"
        )
        return ActionResponse(
            Action.RESPONSE,
            response="番茄钟没关掉，定时服务可能不在线。",
        )

    if resp.status_code == 200:
        # 和 start_pomodoro 一样：确认语说完就收掉会话，设备回 idle。
        # timer_cancel / timer_complete 已经把 HUD 收掉，不会再有红字。
        conn.close_after_chat = True
        logger.bind(tag=TAG).info(
            f"stop_pomodoro {mode} 成功 | device={device_id} | "
            f"job={job_id} | phase={phase}"
        )
        if mode == "cancel":
            return ActionResponse(Action.RESPONSE, response="好，这次专注放弃了。")
        if phase == "overtime":
            return ActionResponse(Action.RESPONSE, response="好，番茄钟关了。")
        return ActionResponse(Action.RESPONSE, response="好，这次专注提前结束了。")

    detail = ""
    try:
        detail = str(resp.json().get("detail", ""))
    except Exception:  # noqa: BLE001
        detail = ""
    logger.bind(tag=TAG).warning(
        f"stop_pomodoro HTTP {resp.status_code} | device={device_id} | "
        f"job={job_id} | detail={detail!r}"
    )
    if resp.status_code == 404:
        return ActionResponse(Action.RESPONSE, response="这个番茄钟已经结束了。")
    return ActionResponse(Action.RESPONSE, response="番茄钟没关掉，稍后再试。")
