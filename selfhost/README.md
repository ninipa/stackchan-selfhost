# 自建服务端

这是 StackChan 的自建服务端：语音走 `xiaozhi-esp32-server`，消息/行为/遥控走
`chan-behavior`。不需要官方云账号。

## 依赖

- Docker Engine / Docker Desktop / OrbStack，**Compose v2**
- 建议 4GB 内存、8GB 更稳；磁盘至少 5GB（镜像 + ASR 模型约 893MB）
- 宿主机 Python 3.9+（只有配置页需要；容器内 Python 由镜像自带）
- 可选：Tailscale（手机远程访问）、和风天气 key、DeepSeek key

## 快速开始

```bash
cd selfhost
cp deploy.env.example deploy.env       # 手填也可以；配置页会自动生成 token
```

推荐用配置页：

```bash
python3 -m venv ../.venv
../.venv/bin/pip install -r setup/requirements.txt
../.venv/bin/python -m uvicorn app:app --app-dir setup --host 127.0.0.1 --port 8200
# 或从仓库根目录：./selfhost/setup/run.sh
```

打开 `http://127.0.0.1:8200`，填 `DEVICE_HOST`、`PUBLIC_BASE_URL`、LLM、天气、时区、人设，
点「保存并重启」。第一次保存自动生成 `APP_TOKEN` / `DEVICE_TOKEN`，底部卡片给出固件和 App 要用的值。

不用配置页时：

```bash
./tools/render-config.sh --check
./tools/render-config.sh --write
docker compose up -d
./tools/doctor.sh
```

## 端口

| 端口 | 服务 | 谁访问 |
|---|---|---|
| 8000 | 语音 WebSocket | 设备 |
| 8003 | OTA + 视觉接口 | 设备 |
| 8100 | 行为服务 / App / 遥控 | 设备 + 手机 |
| 8200 | 配置页 | 仅宿主机 localhost（SSH 隧道） |

不要把 8000/8003/8100 直接转发到公网。手机外网访问用 Tailscale，不要开 Funnel。

## 数据与密钥

- `data/`、`models/*.pt`、`behavior/data/`、`behavior/.env`、`deploy.env`、`.env` 都不入库。
- provider key 只写不读：配置页 GET 只返回掩码；真正 key 在 `deploy.env` / `data/.config.yaml`。
- 整片数据/照片/DB 不要提交，也不要发到公开群。

## 镜像与补丁

`docker-compose.yml` pin 了实测 digest，并把 `patches/` 的 7 个文件只读挂载进容器。
换镜像/tag 后必须重新验证补丁；细节见仓库根 `docs/ARCHITECTURE.md` 和 `firmware/VERSIONS.md`。

## 文档

- [`setup/README.md`](setup/README.md)：配置页、保存/回滚、SSH 隧道
- [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)：整体拓扑
- [`../docs/PROTOCOL.md`](../docs/PROTOCOL.md)：设备 ↔ 行为服务协议
