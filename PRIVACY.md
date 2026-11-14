# Privacy

这套系统不向维护者回传数据；但运行时会处理以下本地数据，并调用你配置的第三方服务。

## 本地保存

| 位置 | 内容 |
|---|---|
| `selfhost/deploy.env` | LLM/天气 key、App/Device token、部署地址 |
| `selfhost/data/.config.yaml` | 服务端配置（含 key） |
| `selfhost/data/memory.json` | 开启 `mem_core` 后的对话摘要/事件 |
| `selfhost/behavior/data/chan.db` | 设备、消息、job、番茄钟记录 |
| `selfhost/behavior/data/photos/` | 遥控/拍照产生的照片 |
| `selfhost/behavior/data/audio/` | TTS 缓存（可按 sha1 清理） |
| `selfhost/data/opuss/`、日志 | 调试抓包/运行日志（默认已关 dump） |

这些目录都已在 `.gitignore` 中排除；不要提交。

## 第三方请求

- **LLM**：对话文本会发给你配置的 OpenAI Chat Completions 兼容服务（默认 DeepSeek）。
- **天气**：城市名会发给和风天气。
- **TTS**：要合成的文本会发给 EdgeTTS。
- **手机/设备**：设备走局域网；手机可走 Tailscale。没有本项目自己的遥测。

## 删除数据

```bash
cd selfhost
docker compose down
rm -f data/memory.json
rm -rf behavior/data/photos behavior/data/audio
# 需要彻底重置时再删 behavior/data/chan.db（会丢设备/消息/番茄钟记录）
```

关闭记忆：在配置页把「记忆功能」设为 `nomem`，或在 `deploy.env` 设 `MEMORY=nomem` 后保存/重启。
照片默认只保留最近 100 张 / 30 天；App 遥控页也提供“清空照片”。

## 公开仓库

公开仓库只放源码和脱敏文档；不包含 `dump/`、`data/`、数据库、照片、日志、整片备份或密钥。
如果你 fork 后要公开，先跑 `tools/check-dist.sh`。
