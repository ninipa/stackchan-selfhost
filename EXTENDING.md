# Extending

先读 [`DEVELOPMENT.md`](DEVELOPMENT.md) 的布局和“改配置的原则”。下面按“想加什么”索引。

## 加一个行为 op（服务端脚本 → 设备动作）

1. 服务端：`selfhost/behavior/app/behavior.py` 里生成 step（例如 `{"op": "blink", "ms": 300}`）；
2. 固件：`firmware/main/hal/behavior/behavior_runtime.cpp` 的解析和执行各加一个分支；
3. 协议：更新 [`docs/PROTOCOL.md`](docs/PROTOCOL.md) 的 `behavior` 表；
4. 如果改了字段语义/删字段，按协议版本规则同时 bump：
   - `firmware/main/hal/behavior/behavior_client.cpp` 的 `kBehaviorApiVersion`
   - `selfhost/behavior/app/main.py` 的 `SUPPORTED_API_VERSION`
5. 测试：`fake_device.py` 收脚本；用 `lvsim` 或真机看动作。

动作要幂等、可取消；不要在主循环里阻塞超过一帧。舵机 Y 轴只能 5°–85°。

## 加一个表情 / 配饰

- 皮肤：`firmware/main/stackchan/avatar/skins/default/`（眼睛、嘴、默认脸）；
- 表情枚举/字符串映射：`firmware/main/hal/board/stackchan_display.cc` 的 `kEmotionNames`；
- 配饰：`firmware/main/stackchan/avatar/decorators/` + `UpdateEmotionDecorators`；
- 验收：

  ```bash
  tools/lvsim/run.sh
  # 看 preview/lvsim_sheet.png 的 9 宫格/表情矩阵
  ```

静态表情以 lvsim 为准；最后刷一次真机看亮度和线宽。不要在 `setEmotion()` 外零散地
show/hide 子对象，状态会泄漏。

## 加一个固件 app

- 复制 `firmware/main/apps/app_template/`，改 id/名字/图标；
- 在启动器注册（`app_launcher`）；
- 如果 app 要全屏/触摸，参考 `app_touch_demo`；
- 构建：`cd firmware && ./tools/build.sh`；真机验证用 `flash-app.sh` + `verify-device.sh`。

## 加一个服务端插件

- 新文件放 `selfhost/patches/functions/<name>.py`（插件目录是扫描发现的，不需要覆盖上游）；
- 在 `selfhost/docker-compose.yml` 挂到容器对应目录（只读）；
- 重启 `xiaozhi-esp32-server` 后看日志是否注册；
- 如果插件返回新工具，确认 LLM 的 tool schema/提示词没有超预算。

## 加一个记忆 provider

- 参考 `selfhost/patches/memory/mem_core/`：实现 `type` + `save/retrieve` 接口；
- compose 挂到 `core/providers/memory/<name>/`；
- `selected_module.Memory` 改成新名字（或在配置页/`deploy.env` 选）；
- 注意不要默认把家庭隐私写进公开仓库示例。

## 加一个配置项

按顺序改，缺一处就会不同步：

1. `selfhost/deploy.env.example`（给用户看的说明/占位）；
2. `selfhost/tools/render.py`（校验 + 渲染到模板）；
3. `selfhost/templates/*.template`（YAML/env 里真正落的位置）；
4. `selfhost/setup/app.py`（默认值、校验、GET/POST）；
5. `selfhost/setup/static/index.html`（只有普通用户必须改的才放出来）；
6. `selfhost/tests/test_render.py` + `selfhost/setup/tests/test_setup.py`；
7. 更新 [`selfhost/setup/README.md`](selfhost/setup/README.md)。

原则：key/token 只写不读；普通用户不理解的选项放默认值；不要新增“必填但没人知道”的字段。

## 加 Android 原生能力

- 页面/服务端能做的优先放 `selfhost/behavior/app/static/index.html`，这样不用重发 APK；
- 必须用原生能力时改 `shell/android/app/src/main/java/.../MainActivity.java` + `shell/www`；
- 重新 `npx cap sync android`、`assembleRelease`；按 [`shell/README.md`](shell/README.md) 验签/装真机。

## 安全底线

- 不开放 Y 轴限位、电流/扭矩、电池截止；
- 不把 `server host` / token 做成 NVS 可改项（当前是编译期值）；
- 配置页固定 localhost；不要把 Docker socket 暴露给网页；
- 改完跑 [`TESTING.md`](TESTING.md) 里的服务端/固件门禁。
