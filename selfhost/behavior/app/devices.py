from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket


@dataclass
class DeviceConnection:
    device_id: str
    ws: WebSocket
    name: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # 截图上传：设备把帧按块发过来，这里攒着
    snapshot_buf: bytearray | None = None
    snapshot_meta: dict[str, Any] = field(default_factory=dict)

    async def send_json(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.ws.send_json(payload)

    async def send_bytes(self, data: bytes) -> None:
        async with self._send_lock:
            await self.ws.send_bytes(data)


class DeviceRegistry:
    def __init__(self) -> None:
        self._devices: dict[str, DeviceConnection] = {}

    def register(self, conn: DeviceConnection) -> None:
        self._devices[conn.device_id] = conn

    def unregister(self, device_id: str) -> None:
        self._devices.pop(device_id, None)

    def get(self, device_id: str | None) -> DeviceConnection | None:
        if device_id is None:
            return None
        return self._devices.get(device_id)

    def first_online(self) -> DeviceConnection | None:
        return next(iter(self._devices.values()), None)

    def list(self) -> list[DeviceConnection]:
        return list(self._devices.values())

    def list_online(self, ttl_seconds: float) -> list[DeviceConnection]:
        """最近 ttl_seconds 内有过消息的连接。

        只用于展示/统计（/api/devices、/health）。不要在 registry 里驱逐过期连接：
        消息和番茄钟下发仍靠 registry.get()/first_online()，驱逐会让心跳晚到但仍在线的
        设备被判成 offline，甚至连带 409。
        """
        cutoff = time.time() - ttl_seconds
        return [conn for conn in self._devices.values() if conn.last_seen >= cutoff]


registry = DeviceRegistry()
