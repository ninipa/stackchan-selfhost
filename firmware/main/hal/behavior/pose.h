/*
 * 头部姿态的单一来源（角度单位 0.1°，与舵机一致）。
 *
 * 为什么要有这个文件：这些数原本散在固件常量、服务端 behavior.py 和归一化坐标三处，
 * 结果是同一个"常态位"有 0°/3°/31.5°/32.4° 四个值，改一处就对不上。
 * 现在固件只认这里；服务端 behavior.py 的 HOME_PITCH / NOD_PITCH 必须与之一致，
 * 由 tools/check_pose_constants.py 比对。
 *
 * 2026-11-10 定案：常态位 20°（用户现场比对 31.5/22/20 后选 20）。
 */
#pragma once

namespace chan_pose {

// 会话常态位：发消息、番茄钟表演、困脸退出后都回到这里
constexpr int kPitchHome = 200;

// 点头低点：200 → 60 是 14° 行程。硬件下限 3°，留 3° 余量，别再往下压。
constexpr int kPitchNod = 60;

// 左右转幅度：±40°
constexpr int kYawTurn = 400;

}  // namespace chan_pose
