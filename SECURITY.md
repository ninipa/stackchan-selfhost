# Security

## 适用范围

这套服务端设计给**家庭局域网 / Tailscale tailnet**使用，不设计成公网服务。

- 设备端口 `8000/8003`、行为/App 端口 `8100` 不要直接端口转发到公网。
- 配置页 `8200` 固定只绑 `127.0.0.1`，需要用 SSH 隧道；不要做公网反代。
- `deploy.env`、`data/.config.yaml`、`behavior/.env` 含 key/token，权限应为 600，
  不要提交、截图或发到群里。
- 整片 flash 备份含 Wi-Fi 密码和绑定 token，**不要公开**。

服务端默认关闭 `server.auth.enabled`：同一 LAN/tailnet 内能访问 8100 的人可以访问行为 API。
如果网络不可信，请用防火墙限制来源，或启用服务端认证并把设备 MAC 加入白名单。

## 报告问题

请使用 GitHub 仓库的 **private security advisory**（或私下联系维护者）。
不要在公开 issue 里贴 API key、token、完整日志中的凭据、设备 MAC 或内网地址。

## 如果密钥泄露

1. 立刻在 provider 控制台轮换 DeepSeek / 和风天气 key；
2. 在配置页重新生成 `APP_TOKEN` / `DEVICE_TOKEN`（或删除 `deploy.env` 里对应行后保存）；
3. 重新编译/刷写固件（DEVICE_TOKEN 是编译期值），App 重新填 App Token；
4. 检查 `dump/`、`data/`、日志、截图是否被提交/上传；必要时从 git 历史移除。

## 已知边界

- 配置页能改写配置并重启容器；只在需要时手动运行，用完 `Ctrl+C`。
- 公共源码里的 M5 账号/App Center 认证是 weak 占位实现；不要依赖它做安全边界。
- EdgeTTS 不是有 SLA 的公开 API；不要把敏感内容交给任何第三方 TTS/LLM。
