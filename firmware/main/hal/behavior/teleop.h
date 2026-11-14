#pragma once

#include <cJSON.h>

/**
 * 遥控台（teleop）设备侧 P0
 *
 * 控制面（现有 /device/ws）新增三类消息：
 *   {"type":"teleop","start":true}               开流 + 抢头（AutoAngleSync 关、ModifyLock 开）
 *   {"type":"teleop","x":0.5,"y":-0.2,"speed":600} 归一化目标（x∈[-1,1] 左右，y∈[-1,1] 上下）
 *   {"type":"teleop","center":true}              回正（保持会话）
 *   {"type":"teleop","scan":true}                环视扫描（±90° 往返一次，约 5 秒）
 *   {"type":"teleop","end":true}                 结束会话（关流 + 回中 + 解锁）
 *   {"type":"photo"}                             拍照（VGA 原生 QVGA，q80，快门音）
 *
 * 视频走独立的 /device/video WS（见 selfhost/behavior/app/teleop.py），
 * 与控制面隔离：视频抖动不影响舵机指令。
 *
 * 运动执行复用官方 MOTION 语义 updateMotionFromJson()（yawServo/pitchServo 的 angle+speed），
 * 高频更新期间 AutoAngleSync 关闭（官方 AVATAR 同款做法），2 秒无命令后恢复。
 */
void teleop_start();
/** 主循环调用（由 behavior_runtime_update 驱动）：应用目标、超时回中/结束、扫描 */
void teleop_update();
/** 控制面 type == "teleop" 的 JSON 已解析，root 由调用方释放 */
void teleop_handle_control(cJSON* root);
/** 控制面 type == "photo" */
void teleop_request_photo();
/**
 * 语音活动（点屏幕/开始聆听/说话）时调用：立即释放视频 WS 与网络任务，
 * 把内部 RAM / DMA 让给 I2S（否则首次 listening 可能因 DMA 分配失败崩溃）。
 */
void teleop_on_voice_activity();
