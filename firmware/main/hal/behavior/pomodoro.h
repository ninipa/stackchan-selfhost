#pragma once

#include <cstdint>

/**
 * @brief 番茄钟（屏幕层 + 本机计时）
 *
 * 计时以「服务端下发的剩余时间 + 本机单调时钟」为准：收到 timer_start / timer_sync
 * 时把剩余时间锚一次，之后自己递减，不再依赖墙上时钟（SNTP 校正不会让倒计时跳）。
 *
 * 线程约定：
 *  - apply / cancel 只从 behavior_runtime_update() 调用（已持 LVGL 锁）
 *  - update() 每轮主循环调用一次（已持 LVGL 锁）
 */
void pomodoro_apply(const char* json);
void pomodoro_cancel(const char* job_id);
void pomodoro_update();
/** 气泡/语音要用屏幕：这段时间内把 HUD 收起来（计时照走） */
void pomodoro_yield_ms(uint32_t ms);
bool pomodoro_running();
/** 计时已过 0 点（超时态）：界面该显示困脸 */
bool pomodoro_overtime();

/** 服务端下发 timer_complete：用户从 App 点了完成，设备演一遍"完成" */
void pomodoro_complete(const char* job_id);

// ---------- 长按抽屉 ----------
//
// 触摸在 hal.cpp 的 indev 回调里就被消费了（LVGL 收不到对象点击），
// 所以这里只提供"现在能不能开""开了没""点在哪"三个问题，绘制在 overlay 里。

/** HUD 此刻是否真的显示在屏幕上（说话/气泡让位时为 false） */
bool pomodoro_hud_visible();
bool pomodoro_panel_active();
void pomodoro_panel_open();
void pomodoro_panel_dismiss();
/** 设备坐标下的点击（由 hal.cpp 在松开时调用） */
void pomodoro_panel_tap(int x, int y);
