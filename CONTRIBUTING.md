# Contributing

这是**源码分发**项目，不做 CI、不做 PR 队列。欢迎 fork 后自己改；问题请开 issue。

## 开 issue 时请附

- 你用的是哪个部分：固件 / 服务端 / 配置页 / Android 壳；
- 复现步骤和期望结果；
- `firmware/tools/check-env.sh` 输出（固件问题）；
- 构建日志的关键段（不要贴整段几 MB 日志）；
- 串口日志的关键行（不要贴 token、Wi-Fi 密码、内网地址）；
- 设备型号/系统、ESP-IDF 版本、Docker/Compose 版本、App 版本。

## 不要贴

- API key、`APP_TOKEN`、`DEVICE_TOKEN`、`deploy.env`；
- 整片 flash 备份、`dump/`、`data/`、数据库、照片；
- 真实 MAC、内网 IP、Tailscale 域名（需要时用 `<server>`/`<device>` 代替）。

## 本地门禁

改完至少跑：

```bash
# 服务端
.venv/bin/python -m unittest discover -s selfhost/tests -v
.venv/bin/python -m unittest discover -s selfhost/setup/tests -v

# 固件（需要 ESP-IDF 6.1）
cd firmware && ./tools/verify-all.sh
```

如果 fork 后要公开发布，先在本地检查导出树里没有 `deploy.env`、token、key、`dump/`、`data/`
和照片；本项目维护者用的 `tools/check-dist.sh` 就是做这件事的。

## 许可

上游 M5Stack/StackChan 是 MIT；本仓库的修改也按 MIT 提供。提交即表示你同意按该许可分发。
第三方组件保留各自许可，见 `NOTICE` 和上游仓库。
