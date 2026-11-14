# lvsim：表情 / 行为桌面预览

把 **LVGL 9.4 原样编在主机上**，跑 `firmware/main/stackchan/avatar/skins/default/*.cpp`
和真实的 modifier（Blink / Breath / Idle / Imu / HeadPet / Speaking）。
**改表情先看 lvsim，不刷机。**

## 前置

- 先跑一次固件构建，让 `firmware/managed_components/lvgl__lvgl` 存在：

  ```bash
  cd firmware
  python3 fetch_repos.py
  idf.py -B build6 reconfigure    # 或完整 build
  ```

- C/C++ 编译器：macOS 用 `clang`/`clang++`；Linux 会自动回落到 `cc`/`c++`
  （也可以显式 `CC=… CXX=…`）。
- 出九宫格需要 Pillow：

  ```bash
  pip install pillow
  # 或使用工作区 .venv/bin/python
  ```

## 用法

```bash
bash tools/lvsim/run.sh
```

产物：

- `preview/lvsim_sheet.png`：九宫格
- `preview/lvsim_report.txt`：场景指标（`eye_images` / `mouth dy`）

`face_render` 的四种模式：

| 模式 | 用途 |
|---|---|
| `scenario` | 真跑 Blink/Breath/Idle/Imu/HeadPet/Speaking，逐帧打印指标 |
| `matrix` | 9 表情 × 静息/说话/眨眼（眼睛权重列必须是 -1，不干预） |
| `gallery` | 9 表情 + 配饰 + 摸头/摇晃 |
| `symbols` | 问号/感叹号两档位置对比 |

## 目录

```
tools/lvsim/
├── lv_conf.h      从 LVGL 模板生成（RGB565 / 2MB 堆 / montserrat 16-24）
├── build.sh       编译 LVGL 全部 .c（首次 ~1 分钟，之后增量）
├── build_app.sh   编译 skins/default/*.cpp + 装饰器 + 运动层 + 入口 + 图片资源
├── compile_one.sh 单文件编译（build.sh 内部用）
├── stub/hal/hal.h HAL 桩：虚拟时间 + 摇晃/摸头事件
├── main.cpp       渲染入口
└── run.sh         构建 + 场景 + 矩阵 + 拼图
```

## 平台

- **macOS 已测**；Linux 理论可用（`CC`/`CXX` 回落），九宫格的中文标签可能退回默认字体。
- 输出在 `preview/`，已 gitignore。
- `stub/hal/hal.h` 必须和真 HAL 保持同步：modifier 调用新的 HAL 方法时，
  在 stub 里补一个无副作用的桩（例：`isHeadTouched()` 恒 false）。

## 不要做

- 不要用 PIL/系统字体画“原型图”当验收；那不是同一个渲染器，永远收敛不了。
- 不要靠“刷机 → 拍照 → 描述”调形状；lvsim 跑的是同一份皮肤代码，只差 RGB565 量化和物理屏。
- 不要在 `setEmotion()` 之外零散 show/hide 子对象；可见性只能有一个入口。
