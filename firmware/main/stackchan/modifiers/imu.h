/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once
#include "../modifiable.h"
#include <hal/hal.h>
#include <esp_log.h>
#include <cstdint>

namespace stackchan {

/**
 * @brief
 *
 */
class ImuEventModifier : public Modifier {
public:
    ImuEventModifier(uint32_t reactionDurationMs = 4000) : _reaction_duration_ms(reactionDurationMs)
    {
        _signal_connection = GetHAL().onImuMotionEvent.connect([this](ImuMotionEvent event) {
            if (event == ImuMotionEvent::Shake) {
                _event_shake = true;
            }
        });
    }

    ~ImuEventModifier()
    {
        GetHAL().onImuMotionEvent.disconnect(_signal_connection);
    }

    void _update(Modifiable& stackchan) override
    {
        uint32_t now = GetHAL().millis();

        // 收到晃动事件
        if (_event_shake) {
            _event_shake = false;
            handle_shake_start(stackchan, now);
        }

        // 如果处于晃动反应状态
        if (_is_reacting) {
            if (now >= _next_toggle_tick) {
                _next_toggle_tick = now + 600;

                _toggle_phase      = !_toggle_phase;
                int mouth_rotation = _toggle_phase ? -25 : 25;

                auto& avatar = stackchan.avatar();
                avatar.mouth().setRotation(mouth_rotation);
                avatar.mouth().setWeight(65);
            }

            //  Lock motion modify and move home
            auto& motion = stackchan.motion();
            if (!motion.isModifyLocked()) {
                motion.setModifyLock(true);
                motion.goHome(300);
            }

            // 检查是否结束反应
            if (now >= _restore_at) {
                restore_state(stackchan);
            }
        }
    }

private:
    void handle_shake_start(Modifiable& stackchan, uint32_t now)
    {
        if (!_is_reacting) {
            // 首次触发时，记录状态以便恢复
            _is_reacting = true;

            auto& avatar = stackchan.avatar();

            avatar.setModifyLock(true);
            // 摇晃 → 眩晕表情（螺旋眼），不再把眼睛整个藏起来
            _prev_emotion = avatar.getEmotion();
            avatar.setEmotion(avatar::Emotion::Dizzy);
            _applied_version = avatar.getEmotionVersion();
            // 记下晃动前的姿态：反应结束要还回去，否则头会一直停在 goHome 的 0°
            auto angles = stackchan.motion().getCurrentAngles();
            _prev_yaw    = angles.x;
            _prev_pitch  = angles.y;
            ESP_LOGI(_tag, "shake start, prev emotion %d, pose %d/%d", static_cast<int>(_prev_emotion),
                     static_cast<int>(_prev_yaw), static_cast<int>(_prev_pitch));

            stackchan.avatar().removeDecorator(_dizzy_decorator_id);
            stackchan.avatar().removeDecorator(_shy_decorator_id);

            // 不要在加 Dizzy 表情的同时再叠一个 DizzyDecorator：
            // 表情本身已经在两只眼睛里各画了一个螺旋，装饰器又在 ±70,-16 画两个，
            // 屏幕上就成了"一边两个眼睛、一共四个"。
            _dizzy_decorator_id = -1;
            _shy_decorator_id =
                stackchan.avatar().addDecorator(std::make_unique<avatar::ShyDecorator>(lv_screen_active(), 0));
        }

        // 刷新恢复时间和切换时间
        _restore_at = now + _reaction_duration_ms;
        if (_next_toggle_tick <= now) {
            _next_toggle_tick = now;  // 立即触发第一次嘴巴动作
        }
    }

    void restore_state(Modifiable& stackchan)
    {
        if (!_is_reacting) {
            return;
        }

        auto& avatar = stackchan.avatar();
        avatar.setModifyLock(false);
        avatar.mouth().setWeight(0);
        avatar.mouth().setRotation(0);

        stackchan.avatar().removeDecorator(_dizzy_decorator_id);
        stackchan.avatar().removeDecorator(_shy_decorator_id);
        _dizzy_decorator_id = -1;
        _shy_decorator_id   = -1;

        auto& motion = stackchan.motion();
        motion.setModifyLock(false);
        // 姿态无条件还回去：摇晃反应期间可能没人动过姿势，停在 goHome 的 0° 就是错的
        motion.moveWithSpeed(_prev_yaw, _prev_pitch, 300);

        _is_reacting = false;

        // 反应期间别人改过表情（对话回复、番茄钟表演…）就别再还原，
        // 否则会把几秒前拍下的旧表情盖回去。版本号没变才轮到我们还原。
        if (avatar.getEmotionVersion() != _applied_version) {
            ESP_LOGW(_tag, "shake restore skipped, emotion changed meanwhile (now %d)",
                     static_cast<int>(avatar.getEmotion()));
            return;
        }
        avatar.setEmotion(_prev_emotion);
        ESP_LOGI(_tag, "shake restore -> %d", static_cast<int>(_prev_emotion));
    }

    // 信号相关
    int _signal_connection;
    volatile bool _event_shake = false;

    // 状态控制
    bool _is_reacting          = false;
    bool _toggle_phase         = false;
    uint32_t _restore_at       = 0;
    uint32_t _next_toggle_tick = 0;
    uint32_t _reaction_duration_ms;

    avatar::Emotion _prev_emotion = avatar::Emotion::Neutral;
    uint32_t _applied_version = 0;
    int32_t _prev_yaw         = 0;
    int32_t _prev_pitch       = 0;
    int _dizzy_decorator_id = -1;
    int _shy_decorator_id   = -1;

    static constexpr const char* _tag = "ImuEvent";
};

}  // namespace stackchan
