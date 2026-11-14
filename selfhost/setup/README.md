# StackChan 服务端配置页（宿主机）

FastAPI + 单页，默认 `127.0.0.1:8200`。改 `deploy.env` → render → 重启两个容器 → 健康检查；
失败自动回滚。

## 安全边界

- 配置页**固定只绑 `127.0.0.1`**，`run.sh` 不接受 `SETUP_BIND`；不支持直接暴露到局域网/公网。
- 远程配置只能在笔记本上开 SSH 隧道：

  ```bash
  ssh -N -L 8200:127.0.0.1:8200 user@server
  # 然后浏览器打开 http://127.0.0.1:8200
  ```

  不要把 8200 端口转发、反向代理或 Tailscale Serve 到公网/他人。
- 默认不要求 token（本机用户就是管理员）。多人共用同一台宿主机时，可设
  `SETUP_REQUIRE_TOKEN=1`；token 在 `selfhost/data/setup-admin.token`（600），首次生成时打印。
  它和 `APP_TOKEN` / `DEVICE_TOKEN` 分开。
- **provider key 只写不读**：GET 只返回 `sk-…abcd` 掩码；POST 空值 = 保留原值。
- 服务不挂 `docker.sock`：用当前用户的 `docker` CLI，所以它必须和 Docker 在同一台机器。

## 运行位置

配置页要读写 `selfhost/` 并执行本机 `docker compose`，所以**必须跑在 Docker 所在的那台机器**，
监听 `127.0.0.1:8200`。无头服务器用上面那条 SSH 隧道；不要把页面搬到笔记本上远程调 Docker。

## 安装 / 启动

```bash
.venv/bin/pip install -r selfhost/setup/requirements.txt
./selfhost/setup/run.sh                    # 本机：无 token
# 多人宿主：SETUP_REQUIRE_TOKEN=1 ./selfhost/setup/run.sh
```

本机打开 `http://127.0.0.1:8200` 即可。只有 `SETUP_REQUIRE_TOKEN=1` 时才会打印 admin token；
默认本地不需要。

## 用法

1. 填连接：`DEVICE_HOST`（设备 LAN 地址）+ `PUBLIC_BASE_URL`（手机访问地址）；
2. 填 LLM / 天气 / 时区 / 记忆 / 人设；TTS 音色、结束语、日志级别、服务端认证都用默认值，
   不在页面上暴露；
3. 「测试 LLM / 天气 / OTA」先验证 key 和链路；
4. 「保存并重启」：
   - 写 `deploy.env`（原子 + 600）；
   - 备份 `deploy.env`、`data/.config.yaml`、`data/agent-base-prompt.txt`、`behavior/.env`、`selfhost/.env`、`templates/persona.txt`；
   - `render.py` 生成配置；
   - `docker compose up -d --force-recreate` 重启 `xiaozhi-esp32-server` 和 `chan-behavior`；
   - 60s 内检查 OTA 200 + behavior `/health` 200；
   - 失败恢复备份并重启旧配置。
5. 底部「固件配置」「App 配对」卡片可以直接复制值。

### LLM 端点与模型 ID

只支持 OpenAI **Chat Completions** 兼容端点：

- API URL 填完整地址，以 `/chat/completions` 结尾（页面会自动把 base URL 规范化）；
- 只填 base URL 也能识别；
- `/responses`（OpenAI Responses）、`/messages`（Anthropic）、Gemini 原生 API 不支持，
  需要另加 provider 适配。

Model 填供应商文档里的 **模型 ID**，不是显示名。DeepSeek 当前官方模型是
`deepseek-flash`（便宜快）和 `deepseek-v4-pro`；旧配置里的 `deepseek-chat`
是兼容别名，迁移时只提示、不静默替换。

### 时区

页面只暴露 `TZ`，是下拉选择（常用时区 + 全部 IANA 时区，共约 600 个）。
默认按**服务器系统时区**自动识别；`TZ_OFFSET` 由 `TZ` 自动推导，不再需要手填偏移。
`deploy.env.example` 里的 `TZ` / `TZ_OFFSET` 默认留空，`render.py` 直接跑也会自动识别。

### 天气

可选。建议注册和风天气开发版（<https://console.qweather.com/>），在控制台创建项目后
填它给的 **API Host** 和 **Key**；没有 key 就留空，插件返回不可用，不影响对话。

### Token

页面**不显示 APP_TOKEN / DEVICE_TOKEN 输入框**。第一次保存时服务端自动生成
（`secrets.token_hex(32)`），之后沿用；旧固件的短 `DEVICE_TOKEN` 会保留。
生成的值只出现在底部「固件配置」「App 配对」卡片里：

- `DEVICE_TOKEN` → 复制到 `firmware/local.conf`，改它要重编/重刷固件；
- `APP_TOKEN` → App 首次启动时填。

**第一次部署顺序**：先保存服务端配置（此时生成 token）→ 用「固件配置」卡片的值编译/刷写固件
→ App 首启填「App 配对」卡片的地址和 token。先刷固件再保存配置会拿到旧 token，需要对上。

不要把这两张卡片的内容发到公开群或提交进仓库。

## 从旧配置迁移（mini 首次使用）

如果这台机器还在用旧的 `data/.config.yaml`（1265 行）：

```bash
python3 selfhost/setup/migrate.py                    # 预览，key 掩码
python3 selfhost/setup/migrate.py --profile dev --write
```

- 生成 `deploy.env`（600），已有的会备份成 `deploy.env.bak-*`；
- 旧 `prompt` 写到 `templates/persona.txt.migrated`，并设置 `PERSONA_FILE`；
- 然后打开配置页核对，再「保存并重启」。

作者自己的 mini 用 `--profile dev`；公开用户默认 `prod`。没有 `deploy.env` 时，配置页也会
只读地从旧 config 推断一次，但自定义人设要跑 `--write` 才会保留。

### 旧 DEVICE_TOKEN 兼容

旧固件的 `DEVICE_TOKEN` 可能只有 16 字符（如 `dev-device-token`）。配置页允许**沿用现有短 token**
（只 warning），新增/修改必须 >=32；改 token 要重编固件。
如果绕过配置页直接用 `render-config.sh`，需要加 `--allow-insecure`。
页面不提供 token 输入框；短 token 只会在读取现有 `deploy.env` 时保留。

## 部署模式（内部字段，不在页面里选）

普通用户不用管：默认 `prod`。
作者自己的 mini 在迁移时用：

```bash
python3 selfhost/setup/migrate.py --profile dev --write
```

之后配置页会沿用 `deploy.env` 里的 `COMPOSE_PROFILE=dev`，页面上不显示这个选项。

## 预览 / 测试（不碰 Docker）

```bash
SETUP_DRY_RUN=1 ./selfhost/setup/run.sh
# 保存只写文件，不重启；页面顶部会有 dry-run 标记

.venv/bin/python -m unittest discover -s selfhost/setup/tests -v
```

## 手动启动（推荐）

配置页只是偶尔改配置时用，**不建议常驻**。用完按 `Ctrl+C` 就结束，不留后台进程。

在服务端本机：

```bash
cd /path/to/m5stackchan
./selfhost/setup/run.sh
# 浏览器打开 http://127.0.0.1:8200
```

无头服务器从笔记本一条命令起隧道并前台运行：

```bash
ssh -L 8200:127.0.0.1:8200 user@server \
  'cd /path/to/m5stackchan && ./selfhost/setup/run.sh'
# 浏览器打开 http://127.0.0.1:8200；关掉这条 SSH 就同时停掉配置页
```

默认不需要 admin token：SSH 登录本身就是认证。只有在多人共用同一台宿主机时，
才考虑 `SETUP_REQUIRE_TOKEN=1`。

### 可选：开机自启（不推荐）

如果你确实想让它在登录/开机后常驻，再单独加 launchd/systemd；注意配置页能改配置并重启
容器，长期常驻会多一个本地管理面。不用 Chan 时记得卸载对应的 plist/unit。

## 注意

- 保存会 **force-recreate** 两个容器；xiaozhi 重启要重新加载 ASR 模型（8–10 秒）。
- mini 实测用系统 Python 3.9 也能跑；`run.sh` 会把 `/usr/local/bin` 等 Docker CLI 路径补进 `PATH`，
  否则 launchd/SSH 非交互环境下保存会报“docker 命令不存在”。
- 改 `DEVICE_TOKEN` 后必须重编/重刷固件；改 `APP_TOKEN` 后 App 设置里要同步。
- 页面本身不存 provider key；key 只在 `deploy.env` / `data/.config.yaml` 里（600）。
- 如果容器没起来，页面仍然活着（宿主机服务），可以改回配置再保存。
