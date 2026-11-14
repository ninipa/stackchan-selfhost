#pragma once

// 1 = 去掉唤醒词与 SR 模型，改用触摸说话（内存测量/长期可选项）
// 0 = 保留 "Hi StackChan"
#define CHAN_DISABLE_WAKE_WORD 1

// 诊断开关（A/B）：1 = 跳过每句 sentence_start 的气泡刷新
// 服务端已改为整段合成，一轮只发一次 sentence_start（全文），
// 这里恢复 0 才能显示整段气泡；保留开关便于对比。
#define CHAN_DISABLE_PER_SENTENCE_BUBBLE 0
