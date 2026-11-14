/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "app_touch_demo.h"
#include <hal/hal.h>
#include <mooncake.h>
#include <mooncake_log.h>
#include <smooth_lvgl.hpp>
#include <stackchan/stackchan.h>
#include <stackchan/avatar/decorators/decorators.h>
#include <assets/assets.h>
#include <apps/common/common.h>
#include <esp_heap_caps.h>
#include <memory>

using namespace mooncake;
using namespace smooth_ui_toolkit::lvgl_cpp;
using namespace stackchan;

// 阈值都放在这里：实测手感不对，改这几个数就行
static constexpr uint32_t HOLD_MS = 900;         // 按住超过这个算"按住"，否则松开算轻点
static constexpr uint32_t RELEASE_GAP_MS = 120;  // 假松开的容错窗口
static constexpr uint32_t IDLE_SLEEP_MS = 30000; // 多久没被碰就犯困
static constexpr uint32_t TAP_HAPPY_MS = 1200;   // 轻点后开心持续多久

// 图标数据编在 app 里（icon_pet_data.c），不占 assets 分区
extern "C" const uint8_t icon_pet_map[];
extern "C" const unsigned int icon_pet_map_len;

static constexpr uint16_t ICON_W = 188;
static constexpr uint16_t ICON_H = 150;

AppTouchDemo::AppTouchDemo()
{
    setAppInfo().name = "PET";

    static lv_image_dsc_t icon;
    icon.header.magic  = LV_IMAGE_HEADER_MAGIC;
    icon.header.cf     = LV_COLOR_FORMAT_RGB565A8;
    icon.header.flags  = 0;
    icon.header.w      = ICON_W;
    icon.header.h      = ICON_H;
    icon.header.stride = ICON_W * 2;
    icon.data_size     = icon_pet_map_len;
    icon.data          = icon_pet_map;
    setAppInfo().icon  = (void*)&icon;

    static uint32_t theme_color = 0xFF8A65;
    setAppInfo().userData       = (void*)&theme_color;
}

// 把当前屏幕以 240x180 RGB565 二进制发到串口（调试用）
static void dump_screen()
{
    lv_draw_buf_t* buf = lv_snapshot_take(lv_screen_active(), LV_COLOR_FORMAT_RGB565);
    if (buf == nullptr) {
        printf("SNAPBIN 0 0\nSNAPEND\n");
        return;
    }

    const int ow = 240, oh = 180;
    const int sw = buf->header.w, sh = buf->header.h;
    const uint32_t stride = buf->header.stride;
    const uint8_t* src = buf->data;

    // 注意：这里不能用函数内 static 数组。240*180*2 = 86KB 会落在 .bss，
    // 从开机起就常驻内部 RAM，把 AI.AGENT 的 Wi-Fi / TLS 直接饿死。
    // 改成每次截屏时从 PSRAM 申请，用完就放。
    const size_t out_size = (size_t)ow * oh * 2;
    uint8_t* out = (uint8_t*)heap_caps_malloc(out_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (out == nullptr) {
        lv_draw_buf_destroy(buf);
        printf("SNAPBIN 0 0\nSNAPEND\n");
        return;
    }

    for (int y = 0; y < oh; y++) {
        int sy = y * sh / oh;
        for (int x = 0; x < ow; x++) {
            int sx = x * sw / ow;
            const uint8_t* p = src + sy * stride + sx * 2;
            out[(y * ow + x) * 2]     = p[0];
            out[(y * ow + x) * 2 + 1] = p[1];
        }
    }

    printf("SNAPBIN %d %d\n", ow, oh);
    fwrite(out, 1, out_size, stdout);
    fflush(stdout);
    printf("\nSNAPEND\n");
    fflush(stdout);

    heap_caps_free(out);
    lv_draw_buf_destroy(buf);
}

void AppTouchDemo::onCreate()
{
    mclog::tagInfo(getAppInfo().name, "on create");
}

void AppTouchDemo::set_emotion(avatar::Emotion emotion)
{
    GetStackChan().avatar().setEmotion(emotion);
}

void AppTouchDemo::on_tap()
{
    mclog::tagInfo(getAppInfo().name, "tap");
    set_emotion(avatar::Emotion::Happy);
    // 表情变化本身很细微，加个爱心让它一眼能看出来
    GetStackChan().avatar().addDecorator(std::make_unique<avatar::HeartDecorator>(lv_screen_active(), 900, 500));
    _happy_until = GetHAL().millis() + TAP_HAPPY_MS;
}

void AppTouchDemo::on_hold_start()
{
    mclog::tagInfo(getAppInfo().name, "hold");
    set_emotion(avatar::Emotion::Happy);
    // 用害羞（脸红）和轻点区分开
    GetStackChan().avatar().addDecorator(std::make_unique<avatar::ShyDecorator>(lv_screen_active(), 2000));
    _holding     = true;
    _happy_until = 0;
}

void AppTouchDemo::on_release(uint32_t held_ms)
{
    mclog::tagInfo(getAppInfo().name, "release held={}ms holding={}", held_ms, _holding);

    if (_holding) {
        _holding = false;
        set_emotion(avatar::Emotion::Neutral);
        return;
    }

    // 只要没到按住阈值，松开就一律算轻点（之前 300~900ms 是死区，轻按会完全没反应）
    on_tap();
}

void AppTouchDemo::on_ignored()
{
    mclog::tagInfo(getAppInfo().name, "ignored -> sleepy");
    set_emotion(avatar::Emotion::Sleepy);
    _sleeping = true;
}

void AppTouchDemo::onOpen()
{
    mclog::tagInfo(getAppInfo().name, "on open");

    LvglLockGuard lock;

    auto& stackchan = GetStackChan();

    auto avatar = std::make_unique<avatar::DefaultAvatar>();
    avatar->init(lv_screen_active());
    stackchan.attachAvatar(std::move(avatar));

    stackchan.clearModifiers();
    stackchan.addModifier(std::make_unique<BreathModifier>());
    stackchan.addModifier(std::make_unique<BlinkModifier>());
    // 抚摸（滑动）的反应
    stackchan.addModifier(std::make_unique<HeadPetModifier>());
    // 摇晃的反应
    stackchan.addModifier(std::make_unique<ImuEventModifier>());

    stackchan.motion().setAutoAngleSyncEnabled(false);

    // 左下角一行小字，正常使用时不起眼
    _debug_label = lv_label_create(lv_screen_active());
    lv_label_set_text(_debug_label, "");
    lv_obj_set_style_text_color(_debug_label, lv_color_hex(0x606060), 0);
    lv_obj_align(_debug_label, LV_ALIGN_BOTTOM_LEFT, 8, -8);

    view::create_home_indicator([this]() { close(); }, 0xA0D99C, 0x154311);

    _last_touch_tick = GetHAL().millis();
    _press_tick      = _last_touch_tick;
}

void AppTouchDemo::onRunning()
{
    LvglLockGuard lock;

    // 启动器的屏保是按「全局空闲时间」触发、并且画在所有 app 之上。
    // 这里每帧喂一次活动，屏保就不会盖住这张脸（自己的犯困逻辑不受影响）。
    lv_display_trigger_activity(nullptr);



    // 必须每帧驱动：呼吸、眨眼、摸头反应、摇晃反应都挂在这里面
    GetStackChan().update();
    view::update_home_indicator();

    const bool touched = GetHAL().isHeadTouched();
    const uint32_t now = GetHAL().millis();

    // 轻点后的限时开心，到点回中性
    if (_happy_until != 0 && now >= _happy_until) {
        _happy_until = 0;
        set_emotion(avatar::Emotion::Neutral);
    }

    if (touched) {
        if (!_last_touched) {
            const bool same_gesture = (_last_release_tick != 0 && (now - _last_release_tick) < RELEASE_GAP_MS);
            if (!same_gesture) {
                _press_tick = now;
                _holding    = false;
            }
            _last_touch_tick = now;

            if (_sleeping) {
                _sleeping = false;
                set_emotion(avatar::Emotion::Neutral);
            }
        }

        if (!_holding && (now - _press_tick) >= HOLD_MS) {
            on_hold_start();
        }
    } else {
        if (_last_touched) {
            on_release(now - _press_tick);
            _last_release_tick = now;
        }
        if (!_sleeping && (now - _last_touch_tick) >= IDLE_SLEEP_MS) {
            on_ignored();
        }
    }

    _last_touched = touched;

    // 调试：每 8 秒换一个表情并回传屏幕，方便逐个检查
    if (now - _last_dump > 12000) {  // 彩色帧约 7.5 秒传完，留出余量
        _last_dump = now;
        static const avatar::Emotion seq[] = {avatar::Emotion::Neutral,  avatar::Emotion::Happy,
                                              avatar::Emotion::Laugh,    avatar::Emotion::Angry,
                                              avatar::Emotion::Sad,      avatar::Emotion::Doubt,
                                              avatar::Emotion::Sleepy,   avatar::Emotion::Surprised,
                                              avatar::Emotion::Dizzy};
        static int idx = 0;
        set_emotion(seq[idx]);
        idx = (idx + 1) % 9;
        dump_screen();
    }

    if (_debug_label != nullptr) {
        const int16_t pos = GetHAL().getHeadTouchPosition();
        lv_label_set_text_fmt(_debug_label, "%s %d", touched ? "T" : ".", pos);
    }
}

void AppTouchDemo::onClose()
{
    mclog::tagInfo(getAppInfo().name, "on close");

    LvglLockGuard lock;

    view::destroy_home_indicator();

    if (_debug_label != nullptr) {
        lv_obj_delete(_debug_label);
        _debug_label = nullptr;
    }
}
