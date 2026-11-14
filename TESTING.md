# Testing

## 服务端（不需要设备）

```bash
# 配置渲染
python3 -m unittest discover -s selfhost/tests -v

# 配置页（dry-run，不重启容器）
python3 -m unittest discover -s selfhost/setup/tests -v
```

依赖：Python 3.9+，`pip install -r selfhost/setup/requirements.txt`。

## 固件

```bash
cd firmware
./tools/check-env.sh          # 环境/IDF/依赖
./tools/verify-all.sh         # host tests + lvsim + 构建检查
```

单独构建：

```bash
./tools/configure.sh
./tools/build.sh
```

真机验证：

```bash
./tools/flash-app.sh --yes    # 只写 app + otadata
./tools/verify-device.sh      # 复位并抓日志，检查 BehaviorClient/OTA/内存
```

不要在测试里用 `idf.py flash` 或 `erase-flash`。

## 表情 / 行为预览（lvsim）

```bash
tools/lvsim/run.sh
# 产物：preview/lvsim_sheet.png
```

第一次需要先构建一次固件，让 LVGL 组件存在。`lvsim` 跑的是和真机同一份皮肤代码；
静态表情以它为验收，最后才刷一次真机看亮度/线宽。

## 行为服务（假设备）

```bash
# 终端 1：起行为服务（先生成 selfhost/behavior/.env；见 selfhost/README.md）
cd selfhost/behavior
docker compose up -d

# 终端 2：假设备连控制 WS
python3 tools/fake_device.py
```

再发一条消息：

```bash
curl -s -X POST -H "X-App-Token: $APP_TOKEN" -H "Content-Type: application/json" \
  -d '{"text":"测试","repeat":1,"device_id":"fake-chan"}' \
  http://127.0.0.1:8100/api/messages
```

预期：`/api/jobs` 出现 running → done，假设备收到 `behavior` 和音频帧。

`fake_device.py` 需要 `websockets`；没有就 `pip install websockets`。

## App UI 预览（不动真机）

```bash
python3 selfhost/behavior/tools/ui_preview.py --port 8111
# http://127.0.0.1:8111/app?demo=running
```

## Android 壳

```bash
cd shell
npm ci
npx cap sync android
cd android
JAVA_HOME=<JDK 21> ./gradlew assembleRelease
```

装到手机后至少验证：

- 首启填地址 + App Token；
- 杀掉再开：只出现“正在连接 StackChan…”然后进 App，不跳浏览器；
- 照片“保存到手机”；
- 断开服务器时出现原生错误页和“重新配置服务器”。

## 公开导出（维护者）

```bash
./tools/export-public.sh /tmp/public-check
./tools/check-dist.sh /tmp/public-check
```

门禁应 0 失败；如果导出树缺 `firmware/`/`LICENSE`，会有警告，最终必须在完整 fork checkout 里跑。
