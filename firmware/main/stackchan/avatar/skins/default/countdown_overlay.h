/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once
#include <lvgl.h>
#include <memory>
#include <smooth_lvgl.hpp>
#include <uitk/short_namespace.hpp>
#include <vector>

namespace stackchan::avatar {

/**
 * @brief 番茄钟的屏幕层：底部信息带 / 剩余 20% 的全屏引线 / 超时。
 *
 * 刻意独立于 DefaultAvatar：它是 HUD，不是脸的一部分，显隐由行为运行时控制。
 * 设计约束（见 docs/pomodoro-state-matrix-2026-11-08.md）：
 *  - 引线用 LVGL 图元画，不用贴图（烧到任意进度都能表达，也不吃 flash）
 *  - 更新频率 ≤ 2Hz：只改「已烧段的点数」和火花位置，避免和 I2S/Opus 抢资源
 */
class DefaultCountdownOverlay {
public:
    explicit DefaultCountdownOverlay(lv_obj_t* parent, int width = 320, int height = 240,
                                    const lv_font_t* text_font = nullptr);
    ~DefaultCountdownOverlay();

    void hide();

    /// 底部信息带（phase = focus 时的常显形态）。progress 0..100
    void showBand(int remaining_ms, const char* label, const char* task, int progress);

    /// 全屏引线（剩余 20%）。progress 0..100，决定烧到第几段
    /// remaining_ms / total_ms：燃烧进度由两者算出（烧掉的比例 = 1 - 剩余/总长）
    /// subtitle：大字下方那行，放任务名（不放"还剩几分钟"，大字已经在数了）
    void showFuse(int remaining_ms, int total_ms, const char* subtitle = "");

    /// 超时：底部信息带换成红字 +05:12
    void showOvertime(int overtime_ms, const char* label = "OVERTIME");

    // ---------- 长按抽屉 ----------
    //
    // 为什么用坐标判定而不是按钮：触摸在 hal.cpp 的 indev 回调里就被消费了，
    // LVGL 的 LV_EVENT_CLICKED 根本不会到达对象（见那处注释）。所以这里只负责画，
    // 命中由 panelHitTest() 按矩形算。

    void showPanel(const char* label, int remaining_ms);
    void hidePanel();
    bool panelVisible() const;

    enum class PanelHit {
        None,      ///< 抽屉内的空白处：不处理
        Complete,  ///< 「完成」
        Abandon,   ///< 「放弃任务」
        Continue,  ///< 「继续专注」或抽屉外任意处：关闭抽屉
    };
    PanelHit panelHitTest(int x, int y) const;

private:
    void build();
    void setBandVisible(bool visible);
    void setFuseVisible(bool visible);
    void setFuseProgress(int progress);
    void setFlame(float x, float y, uint32_t tick);

    int _w;
    int _h;
    const lv_font_t* _font;
    bool _cjk_ok = false;

    std::unique_ptr<uitk::lvgl_cpp::Container> _root;

    // 信息带
    std::unique_ptr<uitk::lvgl_cpp::Container> _band;
    std::unique_ptr<uitk::lvgl_cpp::Label> _band_time;
    std::unique_ptr<uitk::lvgl_cpp::Label> _band_label;
    std::unique_ptr<uitk::lvgl_cpp::Label> _band_task;
    std::unique_ptr<uitk::lvgl_cpp::Container> _bar_fill;

    // 长按抽屉（盖在信息带上）
    std::unique_ptr<uitk::lvgl_cpp::Container> _panel;
    std::unique_ptr<uitk::lvgl_cpp::Label> _panel_label;
    std::unique_ptr<uitk::lvgl_cpp::Label> _panel_time;
    std::unique_ptr<uitk::lvgl_cpp::Container> _panel_done;
    std::unique_ptr<uitk::lvgl_cpp::Label> _panel_done_text;
    std::unique_ptr<uitk::lvgl_cpp::Container> _panel_abandon;
    std::unique_ptr<uitk::lvgl_cpp::Label> _panel_abandon_text;
    std::unique_ptr<uitk::lvgl_cpp::Label> _panel_continue;

    // 引线
    std::unique_ptr<uitk::lvgl_cpp::Container> _fuse;
    std::unique_ptr<uitk::lvgl_cpp::Label> _fuse_time;
    std::unique_ptr<uitk::lvgl_cpp::Label> _fuse_hint;
    std::unique_ptr<uitk::lvgl_cpp::Image> _bomb;
    lv_obj_t* _line_dim = nullptr;
    lv_obj_t* _line_hot  = nullptr;
    lv_obj_t* _line_glow = nullptr;
    static constexpr int kSparks = 6;
    lv_obj_t* _flames[3] = {nullptr, nullptr, nullptr};
    lv_obj_t* _sparks[kSparks] = {nullptr};
    int _spark_life[kSparks]   = {0};
    int _spark_x[kSparks]      = {0};
    int _spark_y[kSparks]      = {0};
    uint32_t _flame_tick = 0;
    std::vector<lv_point_precise_t> _pts;
};

}  // namespace stackchan::avatar
