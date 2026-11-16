/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "hal.h"
#include "chan_snapshot.h"
#include "behavior/behavior_runtime.h"
#include <esp_log.h>
#include <memory>
#include <mooncake_log.h>
#include <nvs_flash.h>

static std::unique_ptr<Hal> _hal_instance;
static const std::string_view _tag = "HAL";

Hal& GetHAL()
{
    if (!_hal_instance) {
        mclog::tagInfo(_tag, "creating hal instance");
        _hal_instance = std::make_unique<Hal>();
    }
    return *_hal_instance.get();
}

void Hal::init()
{
    mclog::tagInfo(_tag, "init");

    // Initialize NVS
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    xiaozhi_board_init();
    xiaozhi_mcp_init();
    head_touch_init();
    io_expander_init();
    rtc_init();
    imu_init();
    servo_init();
    lvgl_init();
}

/* -------------------------------------------------------------------------- */
/*                                   System                                   */
/* -------------------------------------------------------------------------- */
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <system_info.h>
#include <esp_ota_ops.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <esp_mac.h>

void Hal::delay(std::uint32_t ms)
{
    vTaskDelay(pdMS_TO_TICKS(ms));
}

std::uint32_t Hal::millis()
{
    return esp_timer_get_time() / 1000;
}

void Hal::feedTheDog()
{
    vTaskDelay(1);
}

std::array<uint8_t, 6> Hal::getFactoryMac()
{
    std::array<uint8_t, 6> mac;
    esp_efuse_mac_get_default(mac.data());
    return mac;
}

std::string Hal::getFactoryMacString(std::string divider)
{
    auto mac = getFactoryMac();
    return fmt::format("{:02X}{}{:02X}{}{:02X}{}{:02X}{}{:02X}{}{:02X}", mac[0], divider, mac[1], divider, mac[2],
                       divider, mac[3], divider, mac[4], divider, mac[5]);
}

void Hal::reboot()
{
    esp_restart();
}

static void _confirm_ota_image_if_stable()
{
    constexpr uint32_t ota_confirm_delay_ms = 20000;
    static bool ota_confirm_checked         = false;
    if (ota_confirm_checked || GetHAL().millis() < ota_confirm_delay_ms) {
        return;
    }
    ota_confirm_checked = true;

    const esp_partition_t* running = esp_ota_get_running_partition();
    if (running == nullptr) {
        mclog::tagError(_tag, "failed to get running partition for ota confirmation");
        return;
    }

    esp_ota_img_states_t ota_state;
    if (esp_ota_get_state_partition(running, &ota_state) != ESP_OK) {
        mclog::tagError(_tag, "failed to get ota state for partition: {}", running->label);
        return;
    }

    mclog::tagInfo(_tag, "ota confirm check: partition={}, state={}", running->label, static_cast<int>(ota_state));
    if (ota_state == ESP_OTA_IMG_PENDING_VERIFY) {
        mclog::tagInfo(_tag, "ota image is stable, marking current app valid");
        esp_ota_mark_app_valid_cancel_rollback();
    }
}

void Hal::updateHeapStatusLog()
{
    _confirm_ota_image_if_stable();

    static uint32_t last_log_tick = 0;
    if (millis() - last_log_tick < 10000) {
        return;
    }
    last_log_tick = millis();
    SystemInfo::PrintHeapStats();
}

/* -------------------------------------------------------------------------- */
/*                                   Xiaozhi                                  */
/* -------------------------------------------------------------------------- */
#include "board/hal_bridge.h"
#include "behavior/behavior_client.h"
#include "behavior/behavior_runtime.h"
#include "behavior/teleop.h"
#if CONFIG_CHAN_SPIKE_ENABLED
#include "behavior/spike_client.h"
#endif
#include <stackchan/stackchan.h>
#include <apps/common/common.h>
#include <assets/assets.h>

void Hal::xiaozhi_board_init()
{
    mclog::tagInfo(_tag, "xiaozhi board init");

    hal_bridge::xiaozhi_board_init();
}

static void _stackchan_update_task(void* param)
{
    bool is_setup_done = false;

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(20));

        tools::update_reminders();

        LvglLockGuard lock;

        if (!hal_bridge::is_xiaozhi_idle()) {
            vTaskDelay(pdMS_TO_TICKS(100));
        }

        GetStackChan().update();

        if (!hal_bridge::is_xiaozhi_ready()) {
            continue;
        }

        // 控制面：连上行为服务并按脚本执行
        behavior_client_start();
        // 遥控台：视频通道 + 会话状态（控制走上面的行为 WS）
        teleop_start();
#if CONFIG_CHAN_SPIKE_ENABLED
        // 遥控台 spike（临时实验）：独立视频 WS，不影响上面的控制面
        spike_client_start();
#endif
        behavior_runtime_update();
        chan_snapshot_poll();

        if (!is_setup_done) {
            // Setup when xiaozhi ready
            GetHAL().startSntp();
            view::create_home_indicator([]() { GetHAL().requestWarmReboot(0); }, 0x81DBBD, 0x134233);
            view::create_status_bar(0x81DBBD, 0x134233);
            is_setup_done = true;
        }

        view::update_home_indicator();
        view::update_status_bar();
    }
}

void Hal::startXiaozhi()
{
    mclog::tagInfo(_tag, "start xiaozhi");

    auto& motion = GetStackChan().motion();
    motion.setAutoAngleSyncEnabled(true);
    motion.setAutoTorqueReleaseEnabled(true);

    // Setup reminder handler
    tools::on_reminder_triggered().clear();
    tools::on_reminder_triggered().connect([](int id, std::string_view msg) {
        mclog::tagInfo(_tag, "reminder triggered: id: {}, msg: {}", id, msg);
        {
            LvglLockGuard lock;
            auto& avatar = GetStackChan().avatar();
            avatar.addDecorator(std::make_unique<view::ReminderView>(lv_screen_active(), msg));
        }
        hal_bridge::app_play_sound(OGG_NEW_NOTIFICATION);
    });

    // Start stackchan update task
    xTaskCreatePinnedToCore(_stackchan_update_task, "stackchan", 4096, NULL, 3, NULL, 1);

    hal_bridge::start_xiaozhi_app();
}

XiaozhiConfig_t Hal::getXiaozhiConfig()
{
    auto bridge_config = hal_bridge::get_xiaozhi_config();
    return XiaozhiConfig_t{
        .idleShutdownTimeSeconds   = bridge_config.idleShutdownTimeSeconds,
        .allowShutdownWhenCharging = bridge_config.allowShutdownWhenCharging,
        .idleRandomMovementLevel   = bridge_config.idleRandomMovementLevel,
        .startAiAgentOnBoot        = bridge_config.startAiAgentOnBoot,
    };
}

void Hal::setXiaozhiConfig(XiaozhiConfig_t config)
{
    hal_bridge::set_xiaozhi_config({
        .idleShutdownTimeSeconds   = config.idleShutdownTimeSeconds,
        .allowShutdownWhenCharging = config.allowShutdownWhenCharging,
        .idleRandomMovementLevel   = config.idleRandomMovementLevel,
        .startAiAgentOnBoot        = config.startAiAgentOnBoot,
    });
}

uint8_t Hal::getBatteryLevel()
{
    return hal_bridge::board_get_battery_level();
}

bool Hal::isBatteryCharging()
{
    return hal_bridge::board_is_battery_charging();
}

void Hal::factoryReset()
{
    mclog::tagInfo(_tag, "start factory reset");
    ESP_ERROR_CHECK(nvs_flash_erase());
    reboot();
}

/* -------------------------------------------------------------------------- */
/*                                   Display                                  */
/* -------------------------------------------------------------------------- */
#include "board/hal_bridge.h"

void Hal::lvglLock()
{
    hal_bridge::disply_lvgl_lock();
}

void Hal::lvglUnlock()
{
    hal_bridge::disply_lvgl_unlock();
}

void Hal::setBackLightBrightness(uint8_t brightness, bool permanent)
{
    hal_bridge::board_set_backlight_brightness(brightness, permanent);
}

uint8_t Hal::getBackLightBrightness()
{
    return hal_bridge::board_get_backlight_brightness();
}

void Hal::setSpeakerVolume(uint8_t volume, bool permanent)
{
    hal_bridge::board_set_speaker_volume(volume, permanent);
}

uint8_t Hal::getSpeakerVolume()
{
    return hal_bridge::board_get_speaker_volume();
}

/* -------------------------------------------------------------------------- */
/*                                    Lvgl                                    */
/* -------------------------------------------------------------------------- */
#include "board/hal_bridge.h"
#include "behavior/pomodoro.h"
#include <stackchan/stackchan.h>

// 触摸手势判定（2026-11-08 真机实测后改为 indev 级）
//
// 不再依赖 LVGL 的对象命中与 LV_EVENT_CLICKED：同一块面板、连坐标都相同的两次点按，
// 实测有一次出 CLICKED、一次不出（LVGL 内部还要看 act_obj / scroll_obj / long_pr_sent），
// 对"整屏只有一种点击语义"的 agent 模式是多余的复杂度，也是"点了没反应"的第二个来源。
// 这里直接按 indev 的按下/松开跳变判定：一次按下 + 一次松开 = 一次点击，物理上必然成立。
//
// 番茄钟的「完成 / 放弃任务 / 继续专注」抽屉也走这条路：长按开抽屉，
// 抽屉打开时短按按坐标命中（见 pomodoro_panel_tap），不依赖 LVGL 控件。
static constexpr uint32_t kTouchLongPressMs = 700;  // 超过即长按（计时中=开抽屉，否则=中止回复）
static constexpr uint32_t kTouchTapMinGapMs = 300;  // 两次点击的最小间隔（防手抖连击）

static void lvgl_read_cb(lv_indev_t* indev, lv_indev_data_t* data)
{
    hal_bridge::lock();
    auto& bridge_data = hal_bridge::get_data();

    if (bridge_data.touchPoint.num == 0) {
        data->state = LV_INDEV_STATE_RELEASED;
    } else {
        data->state   = LV_INDEV_STATE_PRESSED;
        data->point.x = bridge_data.touchPoint.x;
        data->point.y = bridge_data.touchPoint.y;
        // 这次按下已经送达 LVGL，允许下一轮回到 RELEASED
        bridge_data.touchPressLatched = false;
    }

    hal_bridge::unlock();

    static bool last_pressed   = false;
    static uint32_t press_ms   = 0;
    static bool long_fired     = false;
    static uint32_t last_tap_ms = 0;
    static int32_t last_x       = 0;
    static int32_t last_y       = 0;

    const bool pressed = data->state == LV_INDEV_STATE_PRESSED;
    const uint32_t now = lv_tick_get();

    // [TP] 状态跳变日志，配合驱动层的 [TP] panel 日志定位丢点发生在哪一层
    if (pressed != last_pressed) {
        ESP_LOGI("Hal", "[TP] lvgl %s at (%d,%d)", pressed ? "PRESS" : "RELEASE", (int)data->point.x,
                 (int)data->point.y);
    }

    if (pressed) {
        last_x = data->point.x;
        last_y = data->point.y;
    }

    if (pressed && !last_pressed) {
        press_ms   = now;
        long_fired = false;
    } else if (pressed) {
        // 按住不放：到点触发一次长按（read_cb 每帧都会进来，正好当轮询用）
        if (!long_fired && (now - press_ms) >= kTouchLongPressMs) {
            long_fired = true;
            ESP_LOGI("Hal", "[TP] long-press held %ums", (unsigned)(now - press_ms));
            if (pomodoro_hud_visible()) {
                // 计时中：长按开抽屉（完成 / 放弃任务 / 继续专注）
                pomodoro_panel_open();
            } else {
                hal_bridge::abort_xiaozhi_speaking();
            }
        }
    } else if (last_pressed) {
        // 松开：短按算一次点击
        const uint32_t held_ms = now - press_ms;
        if (!long_fired) {
            if (pomodoro_panel_active()) {
                // 抽屉打开时，点击语义改成按坐标命中；用松手前的最后一帧坐标，
                // 免得 release 事件里 point 被清成 (0,0) 而落到左上角
                pomodoro_panel_tap(last_x, last_y);
            } else if (behavior_runtime_consume_memo_tap()) {
                // 备忘录播报中：这次点击只用来停止提醒，不进入聆听（不亮绿灯）
                last_tap_ms = now;
                ESP_LOGI("Hal", "[TP] tap consumed: memo stop");
            } else if (last_tap_ms != 0 && (now - last_tap_ms) < kTouchTapMinGapMs) {
                ESP_LOGI("Hal", "[TP] tap dropped: debounce %ums", (unsigned)(now - last_tap_ms));
            } else if (!hal_bridge::is_xiaozhi_ready()) {
                ESP_LOGI("Hal", "[TP] tap ignored: xiaozhi not ready");
            } else {
                last_tap_ms = now;
                ESP_LOGI("Hal", "[TP] tap accepted (held %ums)", (unsigned)held_ms);
                hal_bridge::toggle_xiaozhi_chat_state();
            }
        }
    }

    last_pressed = pressed;
}

void Hal::lvgl_init()
{
    mclog::tagInfo(_tag, "lvgl init");

    hal_bridge::disply_lvgl_lock();

    mclog::tagInfo(_tag, "create lvgl touchpad indev");
    lvTouchpad = lv_indev_create();
    lv_indev_set_type(lvTouchpad, LV_INDEV_TYPE_POINTER);
    lv_indev_set_read_cb(lvTouchpad, lvgl_read_cb);
    lv_indev_set_group(lvTouchpad, lv_group_get_default());
    lv_indev_set_display(lvTouchpad, hal_bridge::display_get_lvgl_display());
    // 长按阈值 700ms（默认 400ms 太容易把"慢一点的点按"误判成长按）。
    // 长按的语义是"中止当前回复"，不能和普通点按混在一起。
    lv_indev_set_long_press_time(lvTouchpad, 700);

    hal_bridge::disply_lvgl_unlock();
}

/* -------------------------------------------------------------------------- */
/*                                 Warm Reboot                                */
/* -------------------------------------------------------------------------- */
#include <settings.h>
#include <string_view>

static std::string_view _warm_boot_nvs_ns  = "warm_boot";
static std::string_view _warm_boot_nvs_key = "app_index";

void Hal::requestWarmReboot(int appIndex)
{
    mclog::tagInfo(_tag, "warm reboot request to app index: {}", appIndex);

    {
        Settings settings(_warm_boot_nvs_ns.data(), true);
        settings.SetInt(_warm_boot_nvs_key.data(), appIndex);
    }

    delay(100);
    esp_restart();
}

int Hal::getWarmRebootTarget()
{
    Settings settings(_warm_boot_nvs_ns.data(), false);
    return settings.GetInt(_warm_boot_nvs_key.data(), -1);
}

void Hal::clearWarmRebootRequest()
{
    mclog::tagInfo(_tag, "clear warm reboot request");

    Settings settings(_warm_boot_nvs_ns.data(), true);
    settings.SetInt(_warm_boot_nvs_key.data(), -1);
}
