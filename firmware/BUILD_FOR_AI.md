# 给 AI 的固件构建/刷写约束

> 你是替用户构建、刷写 StackChan K151 固件的助手。**严格按本文件执行，不要自由发挥。**
> 用户机器上的路径/端口/版本可能不同；先跑检查脚本，再继续。

## 硬规则（违反会变砖或泄密）

1. **不要 `idf.py flash`**：`build6/flasher_args.json` 含 `0xa00000: generated_assets.bin`，会覆盖唤醒词模型。只用 `tools/flash-app.sh`。
2. **不要 `erase-flash`**：会清 NVS（Wi-Fi、绑定、舵机零点）和 assets。
3. **不要写 `0xa00000`**：assets 分区不随固件分发。
4. 改配置后**删旧 `sdkconfig`**（`configure.sh` 会做）；否则 `sdkconfig.defaults.local` 不生效。
5. IDF 必须是 **6.1**；5.5 编不过。
6. `env -u PYTHONPATH` 只在 macOS/WorkBuddy 出现 `safe-delete` 报错时用；不是通用步骤。
7. `cmake` / `ninja` 必须存在；IDF 不自带。
8. `fetch_repos.py` 需要 GitHub 网络；中国网络用 `HTTPS_PROXY`。
9. `repos.json` 的 SHA 不要改；patch 必须 apply 成功。
10. 设备原本跑 `ota_1` 时，必须同时写 `0xd000` otadata。
11. 下载模式：长按 RST 约 3 秒；`No serial data received` 时手动按 RST。
12. **Y 轴 5°–85°**；不要手掰通电舵机；整片备份含 Wi-Fi 密码，不要上传。
13. 如果 `local.conf` 看起来是作者的配置（内网 IP + 默认 dev token）：**停下来问用户**，不要直接编。
14. 如果板子不是 K151 / 16MB flash：**停下来**，不要刷。
15. TX buffer：看 `sdkconfig`。`CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER=y` 时静态值不生效；静态时设 `CONFIG_ESP_WIFI_STATIC_TX_BUFFER_NUM=8`。漏了可能 `wifi:mem fail`。

## 停止条件（遇到就停，不要猜）

- `./tools/check-env.sh` 有 `✗`
- `./tools/configure.sh --check` 失败
- `./tools/build.sh` 失败
- `build6/stack-chan.bin` 不存在或 < 2MB
- `./tools/flash-app.sh` 没加 `--yes`（不要自己加）
- 刷完 `./tools/verify-device.sh` 缺 `BehaviorClient` / `free sram` / `DMA` / `OTA`
- 设备黑屏、反复重启、串口无输出

停下来时，把完整日志和 `check-env.sh` 输出交给用户；不要尝试 `erase-flash`、换 IDF、改分区表。

## 执行顺序

```bash
# 0) 环境
idf.py --version          # 必须 v6.1
./tools/check-env.sh

# 1) 配置（用户提供 host/token）
cp local.conf.example local.conf
# 让用户填 CHAN_SERVER_HOST / CHAN_DEVICE_TOKEN
./tools/configure.sh
./tools/configure.sh --check

# 2) 构建
./tools/build.sh
# 期望：build6/stack-chan.bin 存在，约 3.3MB，打印 SHA256

# 3) 刷写（必须用户确认）
./tools/flash-app.sh --yes
# 只写 0x20000 + 0xd000

# 4) 验证
./tools/verify-device.sh
```

## 验收标准

- [ ] `check-env.sh` 全绿（warning 可接受）
- [ ] `configure.sh --check` 返回 0
- [ ] `build.sh` 返回 0，产物 > 2MB
- [ ] `flash-app.sh --yes` 返回 0
- [ ] `verify-device.sh` 返回 0
- [ ] 用户确认设备能对话 / App 能发消息 / 遥控可用

## 不要做

- 不要 `idf.py flash` / `erase-flash`
- 不要写 `0xa00000`
- 不要手动改 `sdkconfig.defaults.local`（改 `local.conf` 后重跑 `configure.sh`）
- 不要改 `repos.json` 的 SHA
- 不要为了“让它连上”去改 token/端口/防火墙；先让用户确认服务器状态
- 不要上传整片备份、日志里的 token、`.env`、`.config.yaml`

## 如果用户没有服务器

构建可以先做；**刷写前要确认服务器已经在 LAN 内跑起来**，否则设备开机后连不上，表现为“刷完没反应”。
