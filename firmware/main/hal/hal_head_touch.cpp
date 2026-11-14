/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "hal.h"
#include "drivers/Si12T/Si12T.h"
#include "board/hal_bridge.h"
#include <mooncake_log.h>
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <atomic>

static const std::string_view _tag = "HAL-HeadTouch";

// 供 app 读取的触摸原始状态（手势信号里不带位置，所以单独存一份）
static std::atomic<bool> _s_head_touched {false};
static std::atomic<int16_t> _s_head_position {0};

// 触摸状态
enum class TouchState { IDLE, TOUCHED, SWIPING };

// 配置参数
struct TouchConfig {
    uint8_t touch_threshold = 1;
    int16_t swipe_threshold = 25;  // 使用百分比，范围-100到100（原 40，实测横向抚摸偏迟钝，下调）
    // 判定"真的在摸"的最低强度。Si12T 每路只有 0~3 四档，实测强度 1 的
    // 噪声（基线漂移、舵机动作）就会让位置在 -100/+100 之间跳，
    // 于是没人碰它也会报 swipe —— 表情会莫名其妙变笑。真实手指基本是 2~3。
    uint8_t swipe_min_intensity = 2;
    // 按下后至少稳定这么久才认滑动，挡掉单次采样的毛刺
    uint32_t swipe_hold_ms = 120;
};

// 触摸数据
struct TouchData {
    uint8_t intensity[3];
    uint32_t timestamp;

    // 计算位置（返回-100到100的整数）
    int16_t get_position() const
    {
        uint16_t total = intensity[0] + intensity[1] + intensity[2];
        if (total == 0) return 0;

        int32_t weighted = intensity[0] * (-100) + intensity[1] * 0 + intensity[2] * 100;
        return static_cast<int16_t>(weighted / total);
    }

    uint8_t get_max_intensity() const
    {
        uint8_t max_val = intensity[0];
        if (intensity[1] > max_val) max_val = intensity[1];
        if (intensity[2] > max_val) max_val = intensity[2];
        return max_val;
    }

    bool is_touched() const
    {
        return get_max_intensity() >= 1;
    }
};

// 手势识别器类
class GestureRecognizer {
public:
    GestureRecognizer() : current_state(TouchState::IDLE), initial_position(0)
    {
    }

    // 更新状态机，返回识别到的手势
    HeadPetGesture update(const TouchData& data)
    {
        HeadPetGesture gesture = HeadPetGesture::None;

        switch (current_state) {
            case TouchState::IDLE:
                if (data.is_touched()) {
                    current_state    = TouchState::TOUCHED;
                    initial_position = data.get_position();
                    pressed_at       = data.timestamp;
                    press_max_intensity = data.get_max_intensity();
                    press_swiped     = false;
                    gesture          = HeadPetGesture::Press;
                    // mclog::tagInfo(_tag, "Touch detected at position: {}", initial_position);
                }
                break;

            case TouchState::TOUCHED:
                if (!data.is_touched()) {
                    current_state = TouchState::IDLE;
                    gesture       = HeadPetGesture::Release;
                    // 记一笔"摸到了但没认出滑动"：判断是灵敏度问题还是手太轻
                    if (!press_swiped) {
                        ESP_LOGI(_tag.data(), "press released without swipe (max intensity %d)",
                                 press_max_intensity);
                    }
                } else {
                    if (data.get_max_intensity() > press_max_intensity) {
                        press_max_intensity = data.get_max_intensity();
                    }
                    // 弱信号 / 刚按下：不判滑动。Si12T 强度只有 0~3 四档，
                    // 强度 1 的噪声就能让位置在 -100/+100 之间跳，触发假抚摸。
                    const bool steady = (data.timestamp - pressed_at) >= pdMS_TO_TICKS(config.swipe_hold_ms);
                    if (!steady || data.get_max_intensity() < config.swipe_min_intensity) {
                        break;
                    }

                    // Check for swipe
                    int16_t current_pos = data.get_position();
                    int16_t delta       = current_pos - initial_position;

                    if (delta > config.swipe_threshold) {
                        current_state = TouchState::SWIPING;
                        gesture       = HeadPetGesture::SwipeForward;
                        press_swiped  = true;
                        ESP_LOGI(_tag.data(), "swipe forward (delta %d, intensity %d)", delta,
                                 data.get_max_intensity());
                    } else if (delta < -config.swipe_threshold) {
                        current_state = TouchState::SWIPING;
                        gesture       = HeadPetGesture::SwipeBackward;
                        press_swiped  = true;
                        ESP_LOGI(_tag.data(), "swipe backward (delta %d, intensity %d)", delta,
                                 data.get_max_intensity());
                    }
                }
                break;

            case TouchState::SWIPING:
                if (!data.is_touched()) {
                    current_state = TouchState::IDLE;
                    gesture       = HeadPetGesture::Release;
                }
                break;
        }

        return gesture;
    }

    void set_config(const TouchConfig& cfg)
    {
        config = cfg;
    }

private:
    TouchConfig config;
    TouchState current_state;
    int16_t initial_position;
    TickType_t pressed_at = 0;
    uint8_t press_max_intensity = 0;
    bool press_swiped           = false;
};

static void _head_touch_update_task(void* param)
{
    mclog::tagInfo(_tag, "start update task");

    si12t_handle_t si12t = (si12t_handle_t)param;
    uint8_t touch_result = 0;
    TouchData data;

    GestureRecognizer recognizer;
    HeadPetGesture gesture;

    vTaskDelay(pdMS_TO_TICKS(200));

    while (1) {
        // Read data
        si12t_read_touch_result(si12t, &touch_result);
        si12t_parse_touch_result_to(touch_result, data.intensity);
        data.timestamp = xTaskGetTickCount();

        _s_head_touched.store(data.is_touched(), std::memory_order_relaxed);
        _s_head_position.store(data.get_position(), std::memory_order_relaxed);

        // Update and fire event
        gesture = recognizer.update(data);
        if (gesture != HeadPetGesture::None) {
            GetHAL().onHeadPetGesture.emit(gesture);
        }

        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

void Hal::head_touch_init()
{
    mclog::tagInfo(_tag, "init");

    auto i2c_bus = hal_bridge::board_get_i2c_bus();

    si12t_config_t si12t_cfg = {
        .i2c_bus  = i2c_bus,
        .dev_addr = SI12T_GND_ADDRESS,
    };
    static si12t_handle_t si12t;
    si12t_init(&si12t_cfg, &si12t);
    si12t_setup(si12t, SI12T_TYPE_LOW, SI12T_SENSITIVITY_LEVEL_3);

    // xTaskCreateWithCaps(_head_touch_update_task, "headtouch", 1024 * 6, si12t, 2, NULL, MALLOC_CAP_SPIRAM);
    xTaskCreatePinnedToCoreWithCaps(_head_touch_update_task, "headtouch", 1024 * 6, si12t, 2, NULL, 1,
                                    MALLOC_CAP_SPIRAM);
}

bool Hal::isHeadTouched()
{
    return _s_head_touched.load(std::memory_order_relaxed);
}

int16_t Hal::getHeadTouchPosition()
{
    return _s_head_position.load(std::memory_order_relaxed);
}
