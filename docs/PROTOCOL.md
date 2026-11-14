# 行为控制面协议（2026-11-13）

> 适用：设备 ↔ `chan-behavior`（`selfhost/behavior/`）的控制面。
> 语音链路（设备 ↔ xiaozhi-esp32-server）是另一套协议，见 `ARCHITECTURE.md`。
> 路径按公开单库布局写：固件在 `firmware/`。

## 1. 版本策略

- `hello` 带 `api_version`（整数）。当前 **1**。
- 旧固件没有该字段 = **0**。服务端 `MIN_API_VERSION = 0`，暂时接受并打 warning。
- `welcome` 带服务端 `api_version`，设备可据此记录/提示。
- 加字段、加消息、加可选参数：**不 bump**。
- 改字段语义、删字段、改状态机：**+1**，并同步：
  - 固件 `main/hal/behavior/behavior_client.cpp` 的 `kBehaviorApiVersion`
  - 服务端 `selfhost/behavior/app/main.py` 的 `SUPPORTED_API_VERSION`
- `hello_error {reason, min}` 预留给拒绝旧固件；把 `MIN_API_VERSION` 调到 1 后生效。
- **配置救不了协议漂移**：固件/服务端必须同时升级，或至少保持向后兼容。

## 2. 控制 WebSocket

`ws://<DEVICE_HOST>:8100/device/ws?token=<DEVICE_TOKEN>`

### 设备 → 服务端

| type | 字段 | 说明 |
|---|---|---|
| `hello` | `device_id`（MAC 无冒号）、`name`、`fw`、`api_version`、`state` | 连接后第一条 |
| `heartbeat` | — | 每 15s |
| `job_started` / `job_done` / `job_error` | `job_id`、`error?` | 脚本执行状态；服务端只认自己的 job |
| `state` | `state` dict | 电量/姿态等（当前可选） |
| `snapshot_begin` / `snapshot_end` | `w`、`h`、`len` | 屏幕截图上传；中间是二进制块 |
| `timer_done` | `job_id` | 到点并演完；服务端转 overtime + 补人声 |
| `timer_fuse` | `job_id` | 进入 20% 引线阶段（只报一次） |
| `timer_finished` | `job_id` | 设备面板点「完成」 |
| `timer_abandon` | `job_id` | 设备面板点「放弃任务」 |

### 服务端 → 设备

| type | 字段 | 说明 |
|---|---|---|
| `welcome` | `server`、`api_version`、`ts` | 连接确认 |
| `hello_error` | `reason`、`min` | 版本过旧时拒绝（当前 MIN=0，不会发） |
| `behavior` | `script` | 声明式脚本：`emotion` / `led` / `move` / `wait` / `say` |
| `audio_begin` → 二进制块 → `audio_end` | 长度/分块 | 脚本音频（Ogg Opus），不走 HTTP |
| `cancel` | `job_id` | 中止当前 job |
| `timer_start` / `timer_sync` | `job_id`、`duration_ms`、`label`、`task` | 番茄钟锚点 / 重连对表 |
| `timer_cancel` / `timer_complete` | `job_id` | 取消 / 完成 |
| `snapshot` / `panel` | — | 请求截图 / 先打开长按面板 |
| `teleop` | `start` / `end` / `keepalive` / `center` / `scan` / `yaw`+`pitch`+`speed` | 遥控控制面 |

### 二进制帧

- `audio_begin` 后按 2048B 分块；设备按 `audio_end` 收口。
- 控制面二进制与文本帧共用一条 WS；不要把视频塞进这条连接。

## 3. 视频 WebSocket

`ws://<DEVICE_HOST>:8100/device/video?token=<DEVICE_TOKEN>`

| 方向 | 消息 | 说明 |
|---|---|---|
| 设备→服务端 | `{"t":"hello","device_id":...}` | 连接后第一条 |
| 设备→服务端 | `{"t":"frame","len":...}` + 二进制 | JPEG 帧 |
| 设备→服务端 | `{"t":"photo","len":...}` + 二进制 + `{"t":"photo_end"}` | 拍照 |
| 设备→服务端 | `{"t":"pong","seq":...}` / `{"t":"stat",...}` | RTT / 内存 |
| 服务端→设备 | `{"t":"ping","seq","ts"}` / `ps` / `cfg` / `stream` / `photo` / `mem` / `fov` | 控制 |

控制走 `/device/ws`，视频只推帧；视频抖动不影响舵机指令。

## 4. App HTTP API

- 鉴权：`X-App-Token: <APP_TOKEN>`（除 `/health`、`/app`、`/api/app_version`）。
- 主要接口：
  - `GET /health`、`GET /app`、`GET /api/app_version`
  - `GET /api/devices`、`GET /api/jobs`
  - `POST /api/messages`、`POST /api/jobs/{id}/cancel`、`POST /api/jobs/{id}/finish`
  - `POST /api/timers`、`GET /api/timers/active`
  - `POST /api/teleop/{start,move,end,keepalive,center,scan,photo,ping}`
  - `GET /api/devices/{id}/last_frame.jpg`、`/video_status`、`/photos`
  - `DELETE /api/devices/{id}/photos`
  - `POST /api/devices/{id}/screenshot`、`GET /api/devices/{id}/photos/{name}/download_url`
  - `GET/POST /api/settings`、`GET /api/maintenance/stats`、`POST /api/maintenance/cleanup`

## 5. 兼容窗口

| 固件 api_version | 服务端行为 |
|---|---|
| 0（无字段） | 接受，warning；新固件铺开后改为拒绝 |
| 1（当前） | 正常 |
| >1 | 服务端应拒绝并返回 `hello_error`，提示升级服务端 |

## 6. 变更流程

1. 先加可选字段/新消息，服务端与固件都能忽略未知字段；
2. 两端都发版后，再改语义或删旧字段，并 bump `api_version`；
3. 更新本文件 + `VERSIONS.md`；
4. `MIN_API_VERSION` 只在确认旧固件已升级后调高。
