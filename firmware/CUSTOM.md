# 这个 fork 改了什么 / 限制

`custom` 分支 = M5Stack/StackChan 1.6.2 + 我们的改动。与上游真正重叠的文件很少
（表情皮肤、`hal.h`、`main.cpp`、`avatar.h` 等 4 个），新增功能尽量走独立文件 + 窄钩子。

主要改动：

- 表情皮肤 / 情绪配饰（heart、tear、question、exclam、Zzz）
- 触摸：按下锁存 + indev 层点击/长按判定（修复丢点）
- 行为控制面：`BehaviorClient` + `behavior_runtime`（emotion / led / move / wait / say）
- 番茄钟设备侧状态机 + 屏幕 HUD（信息带 / 引线 / 超时面板）
- 遥控台：视频 WS、拍照、绝对角度 yaw/pitch、LED 隐私指示
- 音频：解码队列满时阻塞等待，修复回复中途丢字
- 时间：OTA 不再把时区偏移叠进 epoch；默认时区改 UTC0
- 无唤醒词：`main/hal/behavior/chan_features.h` 的 `CHAN_DISABLE_WAKE_WORD 1`
- 行为/遥控地址：由 `tools/configure.sh` 从 `local.conf` 生成，不再有作者 IP 兜底

## 刷写规则

- **只写 app + otadata**：`tools/flash-app.sh` 会写 `0x20000` 和 `0xd000`。
- **不要 `idf.py flash`**：会把 `generated_assets.bin` 写到 `0xa00000`，覆盖唤醒词模型。
- **不要 `erase-flash`**：会清 NVS（Wi-Fi、绑定、舵机零点）和 assets。
- assets 分区不随固件分发；对方的设备保留自己的 assets。

## 已知限制

- **M5 账号 / App Center / BLE 握手 / 局域网 Avatar WS 不可用**：公共源码的
  `main/hal/utils/secret_logic/secret_logic.cpp` 是 weak 占位实现（`hi-stack-chan`），
  没有生产密钥。自建服务端 + OTA 响应没有 `activation` 段，所以不需要绑定。
- **唤醒词默认关闭**：`CHAN_DISABLE_WAKE_WORD 1`，用点屏幕/摸头开始说话。
  改回 0 需要重新确认 assets 里的 `wn9_histackchan_tts3` 模型存在。
- **AI 语音链路依赖自建 xiaozhi server**（`selfhost/`），不连官方云。
- **只支持 K151 / 16MB**；CoreS3 / CoreS3-SE / CoreS3-Lite 不适用。
- **Y 轴 5°–85°** 是硬限位；代码里还有更紧的双端 clamp。
- **OTA 只在开机问一次路**；改服务器地址后必须重启设备。

## 回滚

- 有整片备份：写回整片（会连 Wi-Fi/绑定一起恢复）。
- 没有备份：把官方 app 镜像写到 `0x20000`；不要 erase。
- 回滚后如果要回 StackChan World，按官方流程重新绑定。

## 许可

固件基于 M5Stack/StackChan，MIT License；保留上游版权头。
第三方依赖的许可见 `NOTICE`（发版时补）。
