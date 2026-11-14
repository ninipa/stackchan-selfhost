#pragma once

#include <string>

// 控制面 WebSocket 客户端：连接 chan-behavior，收行为脚本、回 job 状态
void behavior_client_start();
bool behavior_client_connected();
void behavior_client_send_json(const std::string& json);
bool behavior_client_send_binary(const void* data, size_t len);
