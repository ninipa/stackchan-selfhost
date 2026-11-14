# 架构：设备 / 服务端 / App（2026-11-13）

> 路径按公开单库布局写：`firmware/`、`selfhost/`、`shell/`、`tools/`。

## 1. 拓扑

```
                    ┌──────────────────────────────┐
                    │  StackChan（K151, ESP32-S3）  │
                    └───────┬───────────┬──────────┘
        voice WS :8000      │           │  control WS :8100
        OTA HTTP :8003      │           │  video WS  :8100
                            ▼           ▼
                 ┌────────────────┐  ┌─────────────────────┐
                 │ xiaozhi-server │  │  chan-behavior      │
                 │ ASR/LLM/TTS    │  │  FastAPI + SQLite   │
                 └────────────────┘  └───────┬─────────────┘
                                             │ HTTPS（Tailscale Serve / Caddy）
                                             ▼
                                      ┌──────────────┐
                                      │ 手机 / App   │
                                      │ WebView/浏览器│
                                      └──────────────┘
```

- **设备 → server**：局域网（`DEVICE_HOST`）。设备不跑 Tailscale。
- **手机 → server**：Tailscale HTTPS（`PUBLIC_BASE_URL`）或 LAN。
- 设备只在开机时问一次 OTA；改服务器地址后必须重启设备。

## 2. 端口

| 端口 | 服务 | 谁访问 |
|---|---|---|
| 8000 | xiaozhi WebSocket（语音） | 设备 |
| 8003 | OTA + 视觉接口 | 设备 |
| 8100 | chan-behavior（控制/视频/App API/静态页） | 设备 + 手机 |
| 8200 | 服务端配置页（`selfhost/setup/`，宿主机服务） | 仅服务器 localhost（SSH 隧道） |

## 3. 组件

### 固件（`firmware/`）

- `main/apps/`：启动器与内置应用（Setup、AI Agent、AVATAR、DANCE…）。
- `main/stackchan/avatar/`：表情皮肤（`skins/default/`）、配饰装饰器、运动层。
- `main/hal/behavior/`：`BehaviorClient`（控制 WS）、`behavior_runtime`（脚本执行器）、
  `teleop`（视频/拍照/遥控）、`pomodoro`（番茄钟状态机）、`behavior_audio`。
- `main/hal/board/`：板级 HAL、显示、触摸、舵机、相机、LED。
- `main/hal/utils/secret_logic/`：M5 账号/App Center 的 weak 占位实现（公共源码缺生产密钥）。

### 服务端（`selfhost/`）

- `docker-compose.yml`：`xiaozhi-esp32-server` 单容器（8000/8003）+ 7 个挂载补丁。
- `behavior/`：`chan-behavior` FastAPI（8100），SQLite `data/chan.db`，静态 App 页面。
- `setup/`：宿主机配置页（8200，固定只绑 127.0.0.1，手动前台运行）；写 `deploy.env` → render → 重启容器 → 健康检查/回滚。
- `patches/`：DeepSeek Search、`mem_core` 记忆、TTS 流式/整段合成、Opus 32k 等。
- `data/.config.yaml`：由 `deploy.env` + `render.py` 生成，不手改。
- `models/SenseVoiceSmall/model.pt`：本地 ASR，893MB，不入库。

### App（`shell/`）

- Capacitor 8 WebView；页面由 `chan-behavior` 提供，改 UI 不需要重发 APK。
- 首次启动只负责：服务器地址 + App Token；之后配置在页面里。
- 照片下载走服务端签名 URL，App 不长期持有 token。

## 4. 三条主链路

### 语音

```
设备 → OTA :8003（拿 websocket.url）→ WS :8000 → ASR → LLM → TTS → 设备播放
```

- OTA 响应没有 `activation`/`mqtt` 段 → 不绑定、走 WebSocket。
- 服务端 `batch_mode + stream_mode`：LLM 全文到齐后一次 TTS，边收边编码。
- 设备只负责解码播放；音色/人设/提示词都在服务端。

### 消息 / 行为脚本

```
App → POST /api/messages → chan-behavior → WS behavior{script}
   → 设备执行 emotion/led/move/wait/say → job_started/job_done → App 历史
```

- 音频随 `audio_begin`/二进制/`audio_end` 下发，不走 HTTP。
- 设备掉线时 job 标 `pending_resend`，重连 10 分钟内重放。

### 番茄钟

```
App → POST /api/timers → DB 锚点 → WS timer_start
   → 设备侧倒计时/HUD → timer_done → 服务端转 overtime + 合成人声
   → 用户点完成/我知道了 → done（early/on_time/late）或 10 分钟后 expired
```

- 状态机在设备侧；服务端只给 `started_at + duration` 锚点，重连用 `timer_sync` 对表。
- TTS 用模板 + 缓存，不调 LLM。

### 遥控

```
App → POST /api/teleop/* → WS teleop{start/move/end/...} → 设备舵机
设备 → video WS :8100 → 服务端 last_frame/照片 → App
```

- 控制与视频分两条 WS；视频抖动不影响舵机指令。
- 会话期间 LED 紫色常亮（隐私指示）；语音优先。

## 5. 配置面

| 层 | 文件 | 生成方式 |
|---|---|---|
| 服务端 | `selfhost/deploy.env` | 手填；`render-config.sh` → `data/.config.yaml`、`behavior/.env`、`selfhost/.env` |
| 设备 | `firmware/local.conf` | 手填；`tools/configure.sh` → `sdkconfig.defaults.local` |
| App | 首次启动 | 服务器地址 + App Token；存 localStorage |
| 设备 NVS | Setup / 首次向导 | Wi-Fi、时区、舵机零点；不改代码 |

**不进配置的东西**：Y 轴 5–85°、板级/引脚/分区、协议版本、包名/签名、每台舵机零点、运行数据。

## 6. 信任边界

- 设备只连 LAN；手机通过 Tailscale 访问；**不要**把 8000/8003/8100 转发公网。
- `DEVICE_TOKEN` 编译进固件；`APP_TOKEN` 只在 App/网页 localStorage。
- 服务端配置页（阶段 4）固定绑 localhost、手动前台运行，key 只写不读。
- OTA 走 HTTP（LAN）；如果暴露到不可信网络，需要 HTTPS + 校验。

## 7. 扩展点

| 想加什么 | 改哪里 | 怎么测 |
|---|---|---|
| 新表情/配饰 | `firmware/main/stackchan/avatar/skins/default/` + `kEmotionNames[]` + Decorator | `tools/lvsim/run.sh` |
| 新行为 op | `firmware/main/hal/behavior/behavior_runtime.cpp` + 服务端脚本 schema | `fake_device.py` + 真机 |
| 新服务端插件 | `selfhost/patches/functions/` | 小脚本 / 真机 |
| 新 App API | `selfhost/behavior/app/main.py` | `ui_preview.py` / curl |
| 新原生能力 | `shell/` + `MainActivity` | 重打 APK |
| 新配置项 | `render.py` schema + 配置页 | `doctor.sh` + 配置页 |
