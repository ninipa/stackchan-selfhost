# 这个 fork 改了什么

> 上游：`M5Stack/StackChan`（MIT）。本 fork 保留上游目录结构，新增 `selfhost/`、`shell/`、
> `tools/`、`docs/`，固件在 `firmware/`（上游路径）。

## 固件

- 表情皮肤 + 9 种表情 + 情绪配饰（爱心/眼泪/问号/感叹号/青筋/Zzz）。
- 触摸输入：按下/松开、按住时长、摇晃、抚摸；头部位置只做弱提示（硬件只有 5 档）。
- 行为终端：设备 ↔ `chan-behavior` 的控制 WS（消息、表情、点头、音频、番茄钟）。
- 遥控：绝对角度 yaw/pitch、视频流、拍照；Y 轴严格限制 5°–85°。
- OTA 地址 / 行为服务地址 / 设备 token 都是**编译期**值，由 `local.conf` 生成，不进 NVS。

## 服务端

- `selfhost/`：xiaozhi-esp32-server 单容器 + 宿主机配置页 + `chan-behavior`。
- 7 个只读挂载补丁（TTS 预缓冲、整段合成、Opus 32k、回复宽限、LLM 重试、MCP 关闭等）。
- 自研 `deepsearch` 联网检索、`mem_core` 记忆 provider、固定结束语。
- 配置页：`deploy.env` → render → 重启容器 → 健康检查 → 失败回滚。
- 配置页固定 `127.0.0.1:8200`，不常驻，按需手动启动。

## Android 壳

- Capacitor 8 WebView，通用包名 `io.github.ninipa.stackchan`。
- APK 不烧服务器地址/token；首启填地址 + App Token。
- 只把已保存服务器 host 留在 WebView，其他外链走系统浏览器；启动时有连接遮罩。
- 照片下载走服务端签名短时 URL，不依赖 WebView 自定义头。

## 分发口径

- **源码分发 + 用户本地构建**；不发预编译固件。
- 不做 CI、不做 PR 队列；欢迎 fork。
- 个人数据/备份/截图/DB/照片不导出到公开树。

## 刷写底线

只写 app 分区 + otadata；不要 `idf.py flash`（会覆盖 assets 里的唤醒词模型），
不要 `erase-flash`（会清 Wi-Fi/绑定/舵机零点）。Y 轴 5°–85°。
