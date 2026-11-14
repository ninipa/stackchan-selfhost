# Changelog

## v0.1.0 — 2026-11-14

### Firmware（StackChan K151 / ESP-IDF 6.1）

- 自定义表情皮肤：9 种表情 + 情绪配饰（爱心、眼泪、问号、感叹号、青筋、Zzz）。
- 触摸输入：按下/松开、按住时长、摇晃、抚摸；头部位置只做弱提示。
- 行为终端：控制 WebSocket（`chan-behavior`）、声明式脚本、表情/点头/LED/音频。
- 遥控：绝对角度 yaw/pitch、视频流、拍照；Y 轴严格限制 5°–85°。
- 番茄钟状态机：focus / fuse / overtime，长按抽屉，到点播报。
- 构建套件：`configure.sh` / `build.sh` / `flash-app.sh` / `verify-device.sh` /
  `verify-all.sh`；只写 app + otadata，不碰 assets/NVS。

### Server（`selfhost/`）

- xiaozhi-esp32-server 单容器 + 7 个只读挂载补丁（TTS 预缓冲/整段合成、Opus 32k、
  回复宽限、LLM 重试、MCP 关闭）。
- `chan-behavior`：FastAPI + SQLite，消息、行为脚本、遥控、番茄钟、照片。
- 宿主机配置页：`deploy.env` → render → 重启容器 → 健康检查 → 失败回滚；
  固定 `127.0.0.1:8200`，手动前台运行，SSH 隧道访问。
- 配置页默认 `deepseek-flash`、只支持 OpenAI Chat Completions、时区下拉/自动识别、
  Token 首次保存自动生成、天气 gzip 处理。
- 自研 `deepsearch` 联网检索、`mem_core` 记忆 provider、固定结束语。

### Android（`shell/`）

- 通用 Capacitor 8 WebView 壳：APK 不烧服务器地址/token；首启填地址 + App Token。
- WebView 放行已保存服务器 host，其他外链走系统浏览器；启动遮罩避免闪配置页。
- 照片签名短时 URL + `DownloadManager` 保存到系统“下载”。
- 包名 `io.github.ninipa.stackchan`，release 签名请使用自己的 keystore。

### Tooling / Docs

- `tools/lvsim`：表情/行为桌面预览，和真机同一份 C++ 皮肤。
- `tools/export-public.sh` / `check-dist.sh`：公开树组装与泄露门禁。
- 公开文档：README（依赖/快速开始）、CUSTOM、DEVELOPMENT、TESTING、EXTENDING、
  SECURITY、PRIVACY、CONTRIBUTING、NOTICE、RELEASE。

### Known limitations

- 只支持 StackChan K151（16MB flash）；CoreS3 其他型号不保证。
- 公共源码的 M5 账号/App Center 认证是 weak 占位实现；不要依赖它。
- EdgeTTS 不是有 SLA 的公开 API。
- 配置页不常驻；需要时手动启动。
- 没有 CI/PR 队列；固件由用户本地构建。
