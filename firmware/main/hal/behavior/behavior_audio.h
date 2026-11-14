#pragma once

#include <cstddef>
#include <cstdint>

// 控制 WS 下发的音频缓存：分块写入 PSRAM，脚本的 say 步骤按 audio_id 取走
void behavior_audio_begin(const char* id, size_t len);
bool behavior_audio_write(const uint8_t* data, size_t len);
void behavior_audio_end(const char* id);
bool behavior_audio_take(const char* id, uint8_t** out_buf, size_t* out_len);
void behavior_audio_clear();
