/*
 * 桌面模拟用的 HAL 桩。
 *
 * 只提供 stackchan 的 modifier / servo 在主机编译时真正用到的那几个接口。
 * include 路径里 stub/ 必须排在 firmware/main 前面，才能盖住真的 hal/hal.h
 * （真 hal.h 会拖进 esp_err / freertos 之类，主机上编不了）。
 */
#pragma once

#include <cstdint>
#include <smooth_ui_toolkit.hpp>
#include <uitk/short_namespace.hpp>

// 与 main/hal/hal.h 保持一致（值要和真机相同，否则事件语义会错）
enum class ImuMotionEvent { None = 0, Shake, PickUp };
enum class HeadPetGesture { None, Press, Release, SwipeForward, SwipeBackward };

class Hal {
public:
    uint32_t millis() const;   // 由模拟器推进的虚拟时间
    void delay(uint32_t ms);

    uitk::Signal<ImuMotionEvent> onImuMotionEvent;
    uitk::Signal<HeadPetGesture> onHeadPetGesture;

    // head_pet.h 的恢复判断会读这个；模拟器没有触摸板，恒 false
    bool isHeadTouched() const { return false; }
};

Hal& GetHAL();
