#include "behavior_audio.h"

#include <esp_heap_caps.h>
#include <esp_log.h>

#include <cstring>
#include <mutex>
#include <string>

#define TAG "BehaviorAudio"

namespace {

constexpr size_t kMaxAudioBytes = 2 * 1024 * 1024;

struct Asset {
    std::string id;
    uint8_t* buf = nullptr;
    size_t cap = 0;
    size_t len = 0;
    bool complete = false;
};

std::mutex s_mutex;
Asset s_asset;

void clear_locked()
{
    if (s_asset.buf != nullptr) {
        heap_caps_free(s_asset.buf);
    }
    s_asset = Asset();
}

}  // namespace

void behavior_audio_begin(const char* id, size_t len)
{
    std::lock_guard<std::mutex> lock(s_mutex);
    clear_locked();

    if (id == nullptr || len == 0 || len > kMaxAudioBytes) {
        ESP_LOGW(TAG, "invalid audio begin: id=%s len=%u", id ? id : "(null)",
                 static_cast<unsigned>(len));
        return;
    }

    auto* buf = static_cast<uint8_t*>(heap_caps_malloc(len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (buf == nullptr) {
        buf = static_cast<uint8_t*>(heap_caps_malloc(len, MALLOC_CAP_8BIT));
    }
    if (buf == nullptr) {
        ESP_LOGW(TAG, "audio alloc failed: %u bytes", static_cast<unsigned>(len));
        return;
    }

    s_asset.id = id;
    s_asset.buf = buf;
    s_asset.cap = len;
    s_asset.len = 0;
    s_asset.complete = false;
    ESP_LOGI(TAG, "begin id=%s len=%u", id, static_cast<unsigned>(len));
}

bool behavior_audio_write(const uint8_t* data, size_t len)
{
    std::lock_guard<std::mutex> lock(s_mutex);
    if (s_asset.buf == nullptr || s_asset.complete) {
        return false;
    }
    if (s_asset.len + len > s_asset.cap) {
        ESP_LOGW(TAG, "audio overflow: %u + %u > %u", static_cast<unsigned>(s_asset.len),
                 static_cast<unsigned>(len), static_cast<unsigned>(s_asset.cap));
        return false;
    }
    memcpy(s_asset.buf + s_asset.len, data, len);
    s_asset.len += len;
    return true;
}

void behavior_audio_end(const char* id)
{
    std::lock_guard<std::mutex> lock(s_mutex);
    if (s_asset.buf == nullptr || id == nullptr || s_asset.id != id) {
        ESP_LOGW(TAG, "audio end mismatch: id=%s", id ? id : "(null)");
        return;
    }
    s_asset.complete = true;
    ESP_LOGI(TAG, "end id=%s len=%u", id, static_cast<unsigned>(s_asset.len));
}

bool behavior_audio_take(const char* id, uint8_t** out_buf, size_t* out_len)
{
    std::lock_guard<std::mutex> lock(s_mutex);
    if (s_asset.buf == nullptr || !s_asset.complete || id == nullptr || s_asset.id != id) {
        return false;
    }
    *out_buf = s_asset.buf;
    *out_len = s_asset.len;

    // 所有权交给调用方，避免 clear 时二次释放
    s_asset.buf = nullptr;
    s_asset = Asset();
    return true;
}

void behavior_audio_clear()
{
    std::lock_guard<std::mutex> lock(s_mutex);
    clear_locked();
}
