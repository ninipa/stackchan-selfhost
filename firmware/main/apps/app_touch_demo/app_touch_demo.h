/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once
#include <mooncake.h>
#include <lvgl.h>
#include <cstdint>
#include <stackchan/avatar/avatar/avatar.h>

/**
 * @brief PET —— 触觉词汇 app
 *
 * 只用实测可靠的通道做判断：
 *   轻点（<300ms）、按住（>=900ms）、抚摸（滑动，交给 HeadPetModifier）、
 *   摇晃（交给 ImuEventModifier）、被冷落（30 秒无接触）。
 *
 * 不做左右定位：Si12T 的 intensity 只有 0~3 四档，position 实际只有 5 个离散值，
 * 且邻块串扰明显（见 AGENTS.md）。左下角保留一行小字用于调试。
 */
class AppTouchDemo : public mooncake::AppAbility {
public:
    AppTouchDemo();


    void onCreate() override;
    void onOpen() override;
    void onRunning() override;
    void onClose() override;

private:
    void on_tap();
    void on_hold_start();
    void on_release(uint32_t held_ms);
    void on_ignored();
    void set_emotion(stackchan::avatar::Emotion emotion);

    lv_obj_t* _debug_label = nullptr;
    bool _last_touched = false;
    bool _holding = false;
    uint32_t _press_tick = 0;
    uint32_t _last_release_tick = 0;
    uint32_t _last_touch_tick = 0;
    uint32_t _happy_until = 0;
    uint32_t _last_dump = 0;
    bool _sleeping = false;
};
