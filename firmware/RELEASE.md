# 发版检查（本地门禁，无 CI）

> 这个项目不做 CI/PR。发版前在一台**干净环境**里按本文件走一遍；
> 任何一步失败都不要打 tag。

## 1. 干净 clone

```bash
git clone <repo> /tmp/stackchan-clean
cd /tmp/stackchan-clean
cp local.conf.example local.conf
# 填测试用 CHAN_SERVER_HOST / CHAN_DEVICE_TOKEN（不要用真实生产 token 发版）
./tools/configure.sh
./tools/configure.sh --check
```

目的：确认仓库里没有漏掉 `templates/`、`repos.json`、patch、`tools/*.sh` 等文件。

## 2. 环境 + 构建

```bash
. "$IDF_PATH/export.sh"       # ESP-IDF v6.1
./tools/check-env.sh
./tools/build.sh
```

期望：

- `check-env.sh` 全绿（warning 可接受）；
- `build.sh` 产出 `build6/stack-chan.bin`，>2MB，打印 SHA256。

## 3. 本地门禁

```bash
./tools/verify-all.sh
```

包含：

- `check-env.sh`
- `build.sh`
- `ctest`（motion 数学）
- `tools/lvsim/run.sh`（macOS；改表情必须过）
- `tools/check-dist.sh`（有则跑）
- `gitleaks`（有则跑）

## 4. 二进制抽查

```bash
strings build6/stack-chan.bin | grep -E '192\.168\.|47\.113\.|api\.tenclass|/device/(ws|video)'
```

- 应只出现**测试配置**的地址；
- 不应出现作者的内网/公网地址、`api.tenclass.net`、真实生产 token；
- 不应出现真实生产 token。

## 5. 真机（建议）

```bash
./tools/flash-app.sh --yes
./tools/verify-device.sh
```

- 从 `ota_0` 启动；
- `BehaviorClient` 连上；
- 能对话 / App 能发消息 / 遥控可用；
- assets 哈希不变（刷前后对比）。

## 6. 发版

- 更新 `VERSIONS.md`（IDF / SHA / 镜像 digest / 验证日期）；
- 写 `CHANGELOG`；
- tag：`vX.Y.Z`；
- 附源码 tarball + `SHA256SUMS`；
- **不发预编译固件**（源码分发是既定口径）；
- 如发 APK，本地构建 + release keystore 签名 + SHA256。

## 7. 发布后

- 在干净环境再 clone 一次 tag，按 `BUILD.md` 走通；
- 记录已知问题；
- 不收 PR；issue 请附 `check-env.sh` + 构建日志 + 串口日志。

## 清理

发版前删掉本机测试产物（都是 gitignored，但别误打包）：

```bash
rm -rf build-verify build-host-tests sdkconfig.bak-* sdkconfig.defaults.local.bak-* local.conf
```
