# 固件故障排查（按报错原文索引）

> 先跑 `./tools/check-env.sh` 和 `./tools/configure.sh --check`，大多数问题在这两步就会暴露。

## 构建环境

### `idf.py: command not found`

```bash
export IDF_PATH="$HOME/esp/esp-idf"
. "$IDF_PATH/export.sh"
```

### `idfX.Y_py3.13_env not found`

`export.sh` 按 PATH 里的 `python3` 推断 venv，而 IDF 的 venv 建在 3.12 上。显式设置：

```bash
export IDF_PYTHON_ENV_PATH="$HOME/.espressif/python_env/idf6.1_py3.12_env"
```

### `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]`

只在 macOS/WorkBuddy 的 safe-delete shim 下出现，用：

```bash
env -u PYTHONPATH idf.py -B build6 build
```

Linux/Windows 不要照搬。

### `cmake` / `ninja` not found

IDF 不打包它们。装进 IDF 的 venv，别动系统：

```bash
"$IDF_PYTHON_ENV_PATH/bin/pip" install cmake ninja
```

### IDF 版本不对

```bash
idf.py --version   # 必须是 v6.1
```

5.5 编不了当前源码；换 6.1。

## 依赖 / patch

### `git apply` patch 失败

`repos.json` 的 xiaozhi SHA 与 patch 不匹配。确认：

```bash
git -C xiaozhi-esp32 rev-parse HEAD
# 应为 49b4380b3faa0ceadd28a3e78aaf0f311c46c57e
```

不要手改 SHA；换 SHA 必须重新生成 patch。

### GitHub 拉不动

```bash
export HTTPS_PROXY=http://127.0.0.1:7890
export HTTP_PROXY=http://127.0.0.1:7890
python3 fetch_repos.py
```

如果是 `Error in the HTTP2 framing layer` 且依赖已经存在，跳过 fetch 直接构建：

```bash
SKIP_FETCH=1 ./tools/build.sh
```

## 配置

### `CONFIG_CHAN_BEHAVIOR_WS_URL 为空：先运行 firmware/tools/configure.sh`

`static_assert` 故意拦截。填 `local.conf` 后重跑：

```bash
./tools/configure.sh
```

### 改了 `local.conf` 但没生效

`configure.sh` 会删/移旧 `sdkconfig`；如果你手动改的 `sdkconfig.defaults.local`，旧的
`sdkconfig` 会覆盖它。重跑：

```bash
./tools/configure.sh
./tools/build.sh
```

### 构建通过但设备连的是旧地址

检查二进制：

```bash
strings build6/stack-chan.bin | grep -E '192\.168\.|/device/(ws|video)|xiaozhi/ota'
```

应只出现你 `local.conf` 里的地址。

## 刷写 / 启动

### `No serial data received`

手动按一次 RST（不是电源键），再重试。下载模式：长按 RST 约 3 秒。

### 刷完黑屏 / 仍在跑官方固件

只写了 app，没写 otadata。用：

```bash
./tools/flash-app.sh --yes
```

它会同时写 `0x20000` + `0xd000`。

### 唤醒词没了

你是不是跑过 `idf.py flash`？它把 `generated_assets.bin` 写到了 `0xa00000`，覆盖了
`wn9_histackchan_tts3`。从整片备份切回 `[0xa00000:0xe00000]` 写回；**不要整片刷别人给的
assets**。本 fork 默认关闭唤醒词，所以即使没有模型也应能点屏幕说话。

### `wifi:mem fail` / `connect_failed native=12`

Wi-Fi TX buffer / DMA 不足。看 `sdkconfig`：

```bash
grep -E 'DYNAMIC_TX_BUFFER|STATIC_TX_BUFFER_NUM' sdkconfig
```

- 如果是 `CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER=y`，静态值不生效，按当前配置走；
- 如果是静态，设 `CONFIG_ESP_WIFI_STATIC_TX_BUFFER_NUM=8`（`configure.sh` 会写）。

## 运行

### `BehaviorClient` 一直不连

按顺序查：

1. `CHAN_SERVER_HOST` 设备可达（同 LAN、2.4GHz、非网页认证网络）；
2. 服务器 `8100` 在听：`curl http://<host>:8100/health`；
3. `CHAN_DEVICE_TOKEN` 与 `selfhost/behavior/.env` 的 `DEVICE_TOKEN` 一致；
4. 服务器防火墙/Security 没拦；
5. 设备串口里 URL 是 `ws://<host>:8100/...`，不是旧 IP。

### OTA 返回官方地址

`sdkconfig.defaults.local` 没生效或 `CONFIG_OTA_URL` 为空。重跑 `configure.sh` + `build.sh`，
并用 `strings` 确认二进制里是 `http://<host>:8003/xiaozhi/ota/`。

### 设备连上但没声音 / 不说话

先看服务端 `xiaozhi-esp32-server` 日志；再看 `.config.yaml` 的 LLM key、TTS voice、
`sample_rate: 16000`。设备只负责解码播放。

### 舵机不动 / 角度很怪

- Y 轴硬限 5°–85°；
- 空闲时扭矩可能被释放，属正常；
- 不要手掰通电的舵机；
- 校准零点在设备 Setup → Servo。

## 安全提醒

- **不要** `erase-flash`：Wi-Fi/绑定/舵机零点全没；
- **不要** 写 `0xa00000`；
- **不要** 上传整片备份（含 Wi-Fi 密码）；
- 刷第三方固件前先解绑。
