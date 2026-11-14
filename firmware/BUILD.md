# 固件构建与刷写（StackChan K151）

> 这是 `M5Stack/StackChan` 的 fork（`custom` 分支）。**只支持 StackChan K151 / 16MB flash**。
> 刷写前先读 `CUSTOM.md` 的安全事项：**只写 app + otadata，不碰 assets/NVS**。

## 0. 你需要

- StackChan K151 + 能传数据的 USB-C 线（优先底座 USB-C 口）
- macOS / Linux / Windows WSL2
- **ESP-IDF v6.1**（不是 5.5）
- Python 3.12（IDF 的 venv 自带）
- 磁盘 ≥ 5GB
- 一台已经在局域网跑起来的服务器（见仓库的 `selfhost/`）

## 1. 安装 ESP-IDF 6.1

按 Espressif 官方文档安装 ESP-IDF **v6.1** 并 source `export.sh`：

```bash
export IDF_PATH="$HOME/esp/esp-idf"     # 换成你的安装路径
. "$IDF_PATH/export.sh"
idf.py --version                        # 必须包含 v6.1
```

如果显示 v5.x，换 6.1；5.5 编不了当前源码。

## 2. 配置这台设备的服务器

```bash
cp local.conf.example local.conf
# 编辑两个值：
#   CHAN_SERVER_HOST    = 设备能访问到的服务器局域网 IP/主机名（不带 http://）
#   CHAN_DEVICE_TOKEN   = 与 selfhost/deploy.env 的 DEVICE_TOKEN 完全一致
./tools/configure.sh
```

`configure.sh` 会：

- 生成 `sdkconfig.defaults.local`（OTA / behavior / teleop 三条 URL + TX buffer）；
- 把旧的 `sdkconfig` 移成 `sdkconfig.bak-<时间>`，强制下一次构建重新读取配置。

**不要手改 `sdkconfig.defaults.local`**；改完 `local.conf` 重新跑 `configure.sh`。

## 3. 检查环境

```bash
./tools/check-env.sh
```

全部 `✓` 才继续；`!` 是 warning。`✗` 时先修环境，不要硬编。

## 4. 构建

```bash
./tools/build.sh
```

它会：

1. `python3 fetch_repos.py`：按 `repos.json` 的 SHA 拉 6 个依赖并打 `patches/xiaozhi-esp32.patch`；
2. `idf.py -B build6 build`；
3. 检查 `build6/stack-chan.bin` 并打印 SHA256。

手动等价命令：

```bash
python3 fetch_repos.py
idf.py -B build6 build
```

网络异常 / 离线：如果 `components/` 和 `xiaozhi-esp32/` 已经拉过，跳过 fetch：

```bash
SKIP_FETCH=1 ./tools/build.sh
```

## 5. 刷写

```bash
./tools/flash-app.sh --yes
```

只写：

- `0x20000` app
- `0xd000` otadata（选择 `ota_0`）

**绝对不要**：

- `idf.py flash`：`flasher_args.json` 会把 `generated_assets.bin` 写到 `0xa00000`，覆盖唤醒词模型；
- `erase-flash`：会清 NVS（Wi-Fi、绑定、舵机零点）。

如果设备原本跑在 `ota_1`，写 `0xd000` 后才会从 `ota_0` 启动；只写 app 不够。

刷完串口没输出：手动按一次 RST，再跑下一步。

## 6. 验证

```bash
./tools/verify-device.sh
```

复位并抓 15 秒日志，检查：

- `BehaviorClient`（控制面连上）
- `free sram` / `DMA`（内存正常）
- `OTA`（开机问路成功）

缺关键行会返回非 0。如果 `BehaviorClient` 没连上，先查：

- `CHAN_SERVER_HOST` 是否设备可达；
- `CHAN_DEVICE_TOKEN` 是否与服务器 `.env` 一致；
- 服务器 `8100` 是否在监听、防火墙是否放行。

## 7. 平台差异

| 平台 | 串口 | 备注 |
|---|---|---|
| macOS | `/dev/cu.usbmodem*` | 不要用 `/dev/tty.*` |
| Linux | `/dev/ttyACM*` | 用户加入 `dialout` 组 |
| Windows | WSL2 + `usbipd` 挂载 | 原生 Windows 未测试 |

`env -u PYTHONPATH` 只在 macOS/WorkBuddy 出现 `safe-delete` 报错时用，不是通用步骤。

## 8. 更新与回滚

**更新**：

```bash
git pull
./tools/configure.sh
./tools/build.sh
./tools/flash-app.sh --yes
```

NVS/assets 不动，Wi-Fi/绑定/舵机零点保留。

**回滚**：

- 有整片备份：写回整片（会连 Wi-Fi/绑定一起恢复）；
- 没有备份：把官方 app 镜像写到 `0x20000`；
- **不要**用 `erase-flash` 当回滚手段。

整片备份含 Wi-Fi 密码和绑定 token，**不要上传/分享**。

## 9. 硬件安全

- Y 轴（俯仰）只能 **5°–85°**；极限角会堵转并永久损坏舵机。
- 不确定舵机是否通电受控时，不要用手掰头。
- 只用 **2.4GHz Wi-Fi**，不能连网页认证网络。
- 换第三方固件前先在 StackChan World / Setup 里解绑。
- 刷机前先整片备份。

## 10. 相关文档

- `BUILD_FOR_AI.md`：给 AI 助手的执行约束与停止条件
- `TROUBLESHOOTING.md`：按报错原文索引
- `VERSIONS.md`：IDF / 依赖 SHA / 服务器镜像版本
- `CUSTOM.md`：这个 fork 改了什么、限制是什么
- `RELEASE.md`：发版前的干净 clone 检查
