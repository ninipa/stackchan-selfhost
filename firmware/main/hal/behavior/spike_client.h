#pragma once

// 遥控台 spike 客户端（临时实验代码，仅 CONFIG_CHAN_SPIKE_ENABLED 时编译/启动）。
// 用途：验证视频 WS 吞吐、Wi-Fi 省电对指令 RTT 的影响、开流内存、摄像头方向。
// 正式功能实现后整文件删除或并入 teleop 模块。
void spike_client_start();
bool spike_client_connected();
