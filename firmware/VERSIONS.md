# 版本与依赖（2026-11-13 实测）

## 固件

| 项 | 值 |
|---|---|
| 基线 | M5Stack/StackChan **1.6.2**（`custom` 分支） |
| `PROJECT_VER` | `1.6.2` |
| 构建框架 | **ESP-IDF v6.1** |
| Python | 3.12（IDF venv） |
| 目标 | StackChan K151 / ESP32-S3 / 16MB flash / 8MB PSRAM |
| 构建目录 | `build6/`（与旧 5.5 的 `build/` 分开） |
| 产物 | `build6/stack-chan.bin`，约 3.3MB |

## 依赖 SHA（`repos.json`）

| 依赖 | SHA |
|---|---|
| mooncake | `572a7e488641f1c3f22e85d65c27f095ecf27d12` |
| mooncake_log | `554d55cd8c6e551b911b99cad274a46929f05bae` |
| smooth_ui_toolkit | `2a18ff5d3fd6b402339d0f4f3c2834f574e3cb05` |
| xiaozhi-esp32 | `49b4380b3faa0ceadd28a3e78aaf0f311c46c57e` |
| ArduinoJson | `733bc4ee82630c88c0a619a883cd3a206efae977` |
| esp-now | `c3b5f9be245f1a80db9cb938600566aa6a8dd1e5` |

`patches/xiaozhi-esp32.patch` 是按 xiaozhi `49b4380` 生成的；换 SHA 必须重新验证 patch。

## 服务端（配套）

| 项 | 值 |
|---|---|
| 镜像 | `ghcr.nju.edu.cn/xinnan-tech/xiaozhi-esp32-server@sha256:d80a172982e8…`（实测） |
| 官方 ghcr.io 当前 index | `sha256:cececd7752e00e044bf9f099751a140834053573bbe6dd55ecbcedcb20c19c25`（**未复测**） |
| 挂载补丁 | `selfhost/patches/` 9 个文件；`MANIFEST.sha256` 校验 |
| 行为服务 | `selfhost/behavior/`，本地构建镜像 |

> 换官方镜像后必须重新验证 7 个覆盖补丁是否仍匹配；`doctor.sh` 只校验文件有没有变，不保证上游兼容。

## App（Android 壳）

| 项 | 值 |
|---|---|
| 版本 | **1.3**（versionCode 4） |
| 包名 | `io.github.ninipa.stackchan` |
| 构建 | Capacitor 8 + `assembleRelease` 实测通过，约 4.1MB |
| 首启 | 本地 `www/index.html` 填 server + App Token；APK 不含 token/host |
| 服务端页面 | `/app#cfg=` 引导；有效 token 无 401，坏 token 显示 401 |
| 签名 | 专用 `stackchan-release.jks`（CN=StackChan Companion），SHA-256 `42ee1f…` |

> fork 后自己发布请换包名 + 新 release keystore；当前 `.jks` 和密码不要外发。

## 更新流程

1. 改 `repos.json` 的 SHA（或改镜像 digest）；
2. `python3 fetch_repos.py`；
3. 若 patch 失败，更新 `patches/xiaozhi-esp32.patch` 并 `git apply --check -R` 自检；
4. `./tools/build.sh`；
5. `./tools/verify-all.sh`；
6. 更新本文件 + `CHANGELOG`。

## 最近一次验证

- 2026-11-13：`build6` 生产配置构建通过（SHA256 `c8eff3b4…`），含 `hello api_version=1`；
- 2026-11-13：协议握手实测：`api_version=1` → `welcome api_version=1`；无字段 → server 打 legacy warning；
- 2026-11-13：生成的测试配置（`192.168.1.20` + 测试 token）构建通过，二进制中无作者 IP/token；
- 2026-11-13：`check-env.sh`、host tests、lvsim、`verify-all.sh --skip-build` 通过；
- 2026-11-13：服务端 behavior 镜像构建、`/health`、`/api/app_version`、`/app`、401/200 鉴权通过。
- 2026-11-13：APK `assembleRelease` 通过（1.3/4，4.1MB，包名 `io.github.ninipa.stackchan`）；
  `assets/capacitor.config.json` 无 `server.url`；`classes.dex` 无旧包名/`ts.net`/64hex/token；
  setup 页正常；`/app#cfg=` 有效 token 无 401、坏 token 401；签名 CN=StackChan Companion。
- 未做：真机安装/照片下载/覆盖升级验证。
