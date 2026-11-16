#pragma once

#include <cstddef>

// 行为运行时（主循环执行）：解析脚本、驱动表情/动作/LED/音频
void behavior_runtime_init();
void behavior_runtime_update();

// 控制客户端回调投递（线程安全，内部拷贝）
void behavior_runtime_enqueue_script(const char* json);
void behavior_runtime_enqueue_cancel(const char* job_id);
// 番茄钟：timer_start / timer_sync 的整包 JSON，以及 timer_cancel / timer_complete
void behavior_runtime_enqueue_timer(const char* json);
void behavior_runtime_enqueue_timer_cancel(const char* job_id);
/** App 上点了「完成」：让设备演一遍完成版（一次点头 + 绿闪 + 气泡） */
void behavior_runtime_enqueue_timer_complete(const char* job_id);
/** 调试/自测用：等价于长按，打开番茄钟抽屉（只在不接真手指时用） */
void behavior_runtime_enqueue_panel();

// 用户唤醒/进入聆听时调用：让行为表演立刻让位给语音
void behavior_runtime_on_voice_activity();

/** 点屏幕：当前是 stop_on_touch 的备忘录脚本时消费这次点击并请求停止，返回 true。 */
bool behavior_runtime_consume_memo_tap();
