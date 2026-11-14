# 开发 / 验证工具

这些工具**不是**运行固件或服务端所必需的，只在改表情、抓日志、做素材时用。

| 工具 | 用途 | 额外依赖 |
|---|---|---|
| `lvsim/` | 在 macOS/Linux 上渲染表情和行为，和真机同一份 C++ 皮肤 | C++ 编译器、LVGL 源码（先构建一次固件） |
| `capture_faces.py` | 真机 SNAPBIN 抓帧并拼联系表 | 固件 PET/SNAPBIN、pyserial |
| `capture_serial.py` | 串口日志抓取 | pyserial |
| `make_symbol_assets.py` | 生成问号/感叹号/Z/眼泪素材 | Python 3 + Pillow |
| `make_icon_pet.py` | 生成 PET 图标素材 | Python 3 + Pillow |
| `check_pose_constants.py` | 检查姿态常量一致性 | Python 3 |
| `analyze_opus_dump.py` | 分析 Opus 抓包 | Python 3 |

## 说明

- `lvsim` 首次运行前需要先构建一次固件，让 `firmware/managed_components/lvgl__lvgl` 存在；
  然后 `tools/lvsim/run.sh`，产物在 `preview/`。
- `capture_faces.py` / `capture_serial.py` 默认串口是 macOS 的 `/dev/cu.usbmodem*`；
  Linux/Windows 用 `--port` 覆盖。
- `snap.py` / `teleop_check.py` 依赖作者自己的设备地址，**没有放进公开树**；需要时按
  `docs/PROTOCOL.md` 自己写一个最小客户端。
- 工具输出（截图、联系表、Opus dump）不要提交进仓库。

## 依赖安装

```bash
python3 -m venv .venv
.venv/bin/pip install pyserial pillow
```

`lvsim` 的构建脚本会在缺少编译器时给出提示。
