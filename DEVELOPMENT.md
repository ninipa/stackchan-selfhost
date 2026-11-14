# Development

## 仓库布局

```
firmware/   M5Stack/StackChan 的 fork（ESP-IDF 6.1）
selfhost/   自建服务端（xiaozhi + chan-behavior + 配置页）
shell/      Android Capacitor 壳
tools/      开发/验证工具
docs/       架构、协议、公开说明
```

## 分支模型

- `main`：跟踪上游 `m5stack/StackChan`，只做快进，不在上面开发。
- `custom`：上游 + 本项目的改动；日常开发、构建、发版都在这条分支。
- 新增的 `selfhost/`、`shell/`、`tools/`、`docs/` 不参与上游固件代码合并，冲突面小。

```bash
git remote add upstream https://github.com/m5stack/StackChan.git
git fetch upstream
git checkout custom
git merge upstream/main
```

第一次合并后先构建，不要直接发版。

## 上游同步

1. 合并 `upstream/main` 到 `custom`；
2. 重新拉固件依赖（`repos.json` 里是固定 SHA）：

   ```bash
   cd firmware
   python3 fetch_repos.py
   ```

3. 如果改了 `firmware/xiaozhi-esp32/` 工作树，重新导出补丁：

   ```bash
   git -C firmware/xiaozhi-esp32 diff > firmware/patches/xiaozhi-esp32.patch
   git -C firmware/xiaozhi-esp32 apply --check -R firmware/patches/xiaozhi-esp32.patch
   ```

4. 构建并跑 `firmware/tools/verify-all.sh`；
5. 更新 `firmware/VERSIONS.md` 和 `CHANGELOG.md`。

不要直接改 `firmware/components/` 或 `firmware/xiaozhi-esp32/` 里的上游文件后忘了导出补丁；
重拉依赖会覆盖它们。

## 代码布局

### 固件

| 路径 | 内容 |
|---|---|
| `firmware/main/apps/` | 启动器与内置 app（Setup、AI Agent、AVATAR、`app_touch_demo`…） |
| `firmware/main/stackchan/avatar/` | 表情皮肤、配饰、运动层 |
| `firmware/main/hal/behavior/` | `BehaviorClient`、脚本执行器、遥控、番茄钟、行为音频 |
| `firmware/main/hal/board/` | 板级 HAL（屏幕、触摸、舵机、相机、LED） |
| `firmware/main/hal/utils/` | OTA、Wi-Fi、secret_logic 等 |

### 服务端

| 路径 | 内容 |
|---|---|
| `selfhost/docker-compose.yml` | xiaozhi 单容器 + 挂载补丁 |
| `selfhost/behavior/app/` | `chan-behavior` FastAPI（消息、行为、遥控、番茄钟） |
| `selfhost/setup/` | 宿主机配置页（`deploy.env` → render → 重启） |
| `selfhost/tools/render.py` | 配置渲染器 |
| `selfhost/patches/` | 上游覆盖补丁 + 新插件/记忆 provider |

## 改配置的原则

- 每台设备不同的值（host、token、key、TZ、城市、人设）→ `deploy.env` / 配置页。
- 发布身份（包名、签名、版本号、App 名）→ 构建时改，不做运行时配置。
- 安全限位（Y 轴 5°–85°、舵机电流、电池截止）→ 代码里写死，**不要开放**。
- 设备身份（MAC/UUID）→ 固件自动读取，不写配置。
- 不要提交 `deploy.env`、`.env`、`data/`、`dump/`、`photos/`、keystore、token.properties。

## 本地门禁

```bash
# 服务端
python3 -m unittest discover -s selfhost/tests -v
python3 -m unittest discover -s selfhost/setup/tests -v

# 固件
cd firmware
./tools/check-env.sh
./tools/verify-all.sh

# 表情预览
tools/lvsim/run.sh
```

公开导出前再跑维护者的 `tools/export-public.sh` + `tools/check-dist.sh`（不在公开树里）。

## 代码风格

- 固件 C++ 跟随上游排版；新增文件尽量独立，补丁钩子保持窄。
- 服务端 Python 用标准库 + 现有依赖；配置校验收口在 `render.py` 和 `setup/app.py`。
- 不要在日志里打印 key/token；配置页 GET 只返回掩码。
