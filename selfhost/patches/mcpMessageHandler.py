"""MCP 消息处理器（补丁：支持暂时关闭设备端 MCP 工具）。

2026-11-09：设备通过 MCP 上报 11 个工具（音量/亮度/主题/拍照/头部/LED/提醒），
描述合计约 2000-3000 token，每轮都发，且描述由固件提供、服务端改不了。
暂时整体关闭，后续把「设备能力」做成独立任务再开。

关闭方式：配置 `disable_device_mcp_tools: true` 时直接丢弃 MCP 消息，
工具就不会注册进 tool_manager，也不会出现在 LLM 的 tools 定义里。
MCP 协议本身不受影响（服务端发出的请求得不到响应，但不阻塞语音链路）。
"""

import asyncio
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType
from core.providers.tools.device_mcp import handle_mcp_message


class McpTextMessageHandler(TextMessageHandler):
    """MCP消息处理器"""

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.MCP

    async def handle(self, conn: "ConnectionHandler", msg_json: Dict[str, Any]) -> None:
        if conn.config.get("disable_device_mcp_tools", False):
            return
        if "payload" in msg_json:
            asyncio.create_task(
                handle_mcp_message(conn, conn.mcp_client, msg_json["payload"])
            )
