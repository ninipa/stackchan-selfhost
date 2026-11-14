// 遥控台 spike 客户端（临时实验代码）。
//
// 与生产控制面（behavior_client）完全隔离：独立 WebSocket、独立 URL，
// 失败不影响语音/行为链路。协议见 tools/spike_receiver.py：
//
//   设备 -> 服务端：hello / pong / stat / stream_ack / frame 头+二进制块 / photo 头+块+photo_end
//   服务端 -> 设备：ping{seq,ts} / ps{mode} / cfg{fps,q,w,h,chunk} / stream{on} / photo / mem / fov{hflip,vflip}
//
// 所有发送动作都在独立任务里，接收回调里只做小回复（pong/ack），避免阻塞收包。

#include "spike_client.h"

#include <board.h>
#include <cJSON.h>
#include <esp_app_desc.h>
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_mac.h>
#include <esp_timer.h>
#include <esp_wifi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <network_error.h>
#include <sdkconfig.h>
#include <web_socket.h>

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>

#include "../board/hal_bridge.h"
#include "image_to_jpeg.h"

// CHAN_SPIKE_WS_URL 依赖 CHAN_SPIKE_ENABLED；关闭时 Kconfig 不会定义它。
// 给空串兜底，避免评估代码影响生产构建（不会回落到任何作者地址）。
#ifndef CONFIG_CHAN_SPIKE_WS_URL
#define CONFIG_CHAN_SPIKE_WS_URL ""
#endif
#if defined(CONFIG_CHAN_SPIKE_ENABLED) && CONFIG_CHAN_SPIKE_ENABLED
static_assert(sizeof(CONFIG_CHAN_SPIKE_WS_URL) > 1,
              "CHAN_SPIKE_ENABLED=1 但 CHAN_SPIKE_WS_URL 为空");
#endif

#define TAG "SpikeClient"

namespace {

std::unique_ptr<WebSocket> s_ws;
std::mutex s_ws_mutex;
std::atomic<bool> s_connected{false};
std::atomic<bool> s_started{false};
std::atomic<bool> s_hello_pending{false};

std::mutex s_frame_mutex;   // 一帧（header + 全部 chunk）整体发送，避免帧间交错
std::mutex s_encode_mutex;  // 保护共享 JPEG 缓冲
std::atomic<bool> s_stream_on{false};
std::atomic<TaskHandle_t> s_stream_task{nullptr};
std::atomic<bool> s_photo_running{false};
std::atomic<uint32_t> s_seq{0};

// 流参数（运行时可被服务端改）
std::atomic<int> s_fps{3};
std::atomic<int> s_quality{50};
std::atomic<int> s_chunk{4096};
std::atomic<int> s_req_w{320};
std::atomic<int> s_req_h{240};

constexpr size_t kJpegCap = 256 * 1024;       // VGA q80 实测 < 100KB，留余量
constexpr size_t kSmallCap = 320 * 240 * 2;   // QVGA RGB565 降采样缓冲

uint8_t* s_jpeg_buf = nullptr;   // PSRAM
uint8_t* s_small_buf = nullptr;  // PSRAM

// ---------- 基础发送 ----------

bool send_json(const std::string& json)
{
    if (!s_connected.load()) {
        return false;
    }
    std::lock_guard<std::mutex> lock(s_ws_mutex);
    if (!s_ws || !s_connected.load()) {
        return false;
    }
    return s_ws->Send(json);
}

bool send_binary(const void* data, size_t len)
{
    if (!s_connected.load()) {
        return false;
    }
    std::lock_guard<std::mutex> lock(s_ws_mutex);
    if (!s_ws || !s_connected.load()) {
        return false;
    }
    return s_ws->Send(data, len, true);
}

void send_mem(const char* stage)
{
    char buf[320];
    snprintf(buf, sizeof(buf),
             "{\"t\":\"stat\",\"stage\":\"%s\",\"int_free\":%u,\"int_min\":%u,\"int_largest\":%u,"
             "\"dma_free\":%u,\"dma_largest\":%u,\"psram_free\":%u}",
             stage,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_DMA),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    send_json(buf);
}

bool send_frame(const char* header, const uint8_t* data, size_t len)
{
    std::lock_guard<std::mutex> lock(s_frame_mutex);
    if (!send_json(header)) {
        return false;
    }
    const int chunk = s_chunk.load();
    for (size_t off = 0; off < len;) {
        const size_t n = std::min(static_cast<size_t>(chunk), len - off);
        if (!send_binary(data + off, n)) {
            return false;
        }
        off += n;
    }
    return true;
}

// ---------- 采集 + 编码 ----------

struct JpegSink {
    uint8_t* data;
    size_t cap;
    size_t len;
    bool valid;
};

size_t jpeg_sink_cb(void* arg, size_t index, const void* data, size_t len)
{
    auto* sink = static_cast<JpegSink*>(arg);
    // 编码器语义：index=0 一次给出完整 JPEG，index=1 是结束哨兵
    if (index == 0 && data != nullptr && len > 0) {
        if (len > sink->cap) {
            ESP_LOGE(TAG, "jpeg %zu > cap %zu", len, sink->cap);
            return 0;
        }
        memcpy(sink->data, data, len);
        sink->len = len;
        sink->valid = true;
    }
    return len;
}

// RGB565 点采样 2x 降采样（源 640x480 -> 目标 320x240）
void downsample2x_rgb565(const uint8_t* src, int sw, uint8_t* dst, int dw, int dh)
{
    const auto* s16 = reinterpret_cast<const uint16_t*>(src);
    auto* d16 = reinterpret_cast<uint16_t*>(dst);
    for (int y = 0; y < dh; ++y) {
        const uint16_t* row = s16 + static_cast<size_t>(y * 2) * sw;
        uint16_t* out = d16 + static_cast<size_t>(y) * dw;
        for (int x = 0; x < dw; ++x) {
            out[x] = row[x * 2];
        }
    }
}

// 抓一帧并编码。rw/rh 为 0 表示用传感器原始分辨率。
bool encode_frame(int rw, int rh, int quality, uint8_t** out, size_t* out_len, int* out_w, int* out_h,
                  int* enc_ms)
{
    auto* cam = hal_bridge::board_get_camera();
    if (cam == nullptr) {
        ESP_LOGE(TAG, "camera is null");
        return false;
    }
    if (s_jpeg_buf == nullptr) {
        ESP_LOGE(TAG, "jpeg buffer is not allocated");
        return false;
    }

    std::lock_guard<std::mutex> enc_lock(s_encode_mutex);

    auto lock = cam->TryLockFrame();
    if (!lock.owns_lock()) {
        return false;
    }
    if (!cam->StreamCaptures()) {
        ESP_LOGW(TAG, "StreamCaptures failed");
        return false;
    }

    const uint8_t* src = cam->GetFrameData();
    const size_t src_len = cam->GetFrameSize();
    const int sw = cam->GetFrameWidth();
    const int sh = cam->GetFrameHeight();
    const auto fmt = cam->GetFrameFormat();
    if (src == nullptr || src_len == 0 || sw <= 0 || sh <= 0) {
        ESP_LOGW(TAG, "invalid frame: len=%zu w=%d h=%d fmt=0x%lx", src_len, sw, sh,
                 (unsigned long)fmt);
        return false;
    }

    const uint8_t* enc_src = src;
    size_t enc_len = src_len;
    int ew = sw;
    int eh = sh;
    if (rw > 0 && rh > 0 && (rw != sw || rh != sh)) {
        if (fmt == V4L2_PIX_FMT_RGB565 && sw == rw * 2 && sh == rh * 2 && s_small_buf != nullptr) {
            downsample2x_rgb565(src, sw, s_small_buf, rw, rh);
            enc_src = s_small_buf;
            enc_len = static_cast<size_t>(rw) * rh * 2;
            ew = rw;
            eh = rh;
        } else {
            ESP_LOGW(TAG, "downsample unsupported: %dx%d fmt=0x%lx -> %dx%d (using full frame)", sw,
                     sh, (unsigned long)fmt, rw, rh);
        }
    }

    JpegSink sink{s_jpeg_buf, kJpegCap, 0, false};
    const uint32_t t0 = esp_timer_get_time();
    const bool ok = image_to_jpeg_cb(const_cast<uint8_t*>(enc_src), enc_len, static_cast<uint16_t>(ew),
                                     static_cast<uint16_t>(eh), fmt, static_cast<uint8_t>(quality),
                                     jpeg_sink_cb, &sink);
    const uint32_t t1 = esp_timer_get_time();
    if (!ok || !sink.valid || sink.len == 0) {
        ESP_LOGW(TAG, "encode failed ok=%d len=%zu", ok, sink.len);
        return false;
    }

    *out = sink.data;
    *out_len = sink.len;
    *out_w = ew;
    *out_h = eh;
    *enc_ms = static_cast<int>((t1 - t0) / 1000);
    return true;
}

// ---------- 流任务 ----------

void stream_task(void*)
{
    ESP_LOGI(TAG, "stream start fps=%d q=%d w=%d h=%d chunk=%d", s_fps.load(), s_quality.load(),
             s_req_w.load(), s_req_h.load(), s_chunk.load());
    send_mem("stream_start");

    uint32_t frames = 0;
    while (s_stream_on.load()) {
        const uint32_t t0 = esp_timer_get_time();
        uint8_t* data = nullptr;
        size_t len = 0;
        int w = 0;
        int h = 0;
        int enc_ms = 0;
        if (encode_frame(s_req_w.load(), s_req_h.load(), s_quality.load(), &data, &len, &w, &h,
                         &enc_ms)) {
            char header[320];
            snprintf(header, sizeof(header),
                     "{\"t\":\"frame\",\"seq\":%u,\"len\":%u,\"w\":%d,\"h\":%d,\"enc_ms\":%d,"
                     "\"int_largest\":%u,\"dma_largest\":%u}",
                     (unsigned)++s_seq, (unsigned)len, w, h, enc_ms,
                     (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
                     (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA));
            if (!send_frame(header, data, len)) {
                ESP_LOGW(TAG, "frame send failed, stop stream");
                break;
            }
            ++frames;
            if ((frames % 15) == 0) {
                send_mem("streaming");
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(50));
        }

        const int fps = s_fps.load();
        if (fps > 0) {
            const uint32_t spent_ms = static_cast<uint32_t>((esp_timer_get_time() - t0) / 1000);
            const uint32_t period_ms = 1000u / static_cast<uint32_t>(fps);
            if (spent_ms < period_ms) {
                vTaskDelay(pdMS_TO_TICKS(period_ms - spent_ms));
            }
        }
    }

    s_stream_on = false;
    s_stream_task = nullptr;
    ESP_LOGI(TAG, "stream stop, %u frames", (unsigned)frames);
    send_mem("stream_stop");
    vTaskDelete(nullptr);
}

void start_stream()
{
    if (s_stream_on.exchange(true)) {
        send_json("{\"t\":\"stream_ack\",\"on\":true,\"note\":\"already\"}");
        return;
    }
    if (s_jpeg_buf == nullptr) {
        s_jpeg_buf = static_cast<uint8_t*>(heap_caps_malloc(kJpegCap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    }
    if (s_small_buf == nullptr) {
        s_small_buf = static_cast<uint8_t*>(heap_caps_malloc(kSmallCap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    }
    if (s_jpeg_buf == nullptr || s_small_buf == nullptr) {
        s_stream_on = false;
        ESP_LOGE(TAG, "alloc stream buffers failed");
        send_json("{\"t\":\"stream_ack\",\"on\":false,\"err\":\"alloc\"}");
        return;
    }
    send_mem("stream_alloc");

    TaskHandle_t task = nullptr;
    if (xTaskCreatePinnedToCore(stream_task, "spike_stream", 8192, nullptr, 4, &task, 1) != pdPASS ||
        task == nullptr) {
        s_stream_on = false;
        ESP_LOGE(TAG, "create stream task failed");
        send_json("{\"t\":\"stream_ack\",\"on\":false,\"err\":\"task\"}");
        return;
    }
    s_stream_task = task;
    send_json("{\"t\":\"stream_ack\",\"on\":true}");
}

void stop_stream()
{
    if (!s_stream_on.exchange(false)) {
        send_json("{\"t\":\"stream_ack\",\"on\":false}");
        return;
    }
    const uint32_t t0 = xTaskGetTickCount();
    while (s_stream_task.load() != nullptr && (xTaskGetTickCount() - t0) < pdMS_TO_TICKS(3000)) {
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    if (s_stream_task.load() != nullptr) {
        ESP_LOGW(TAG, "stream task did not exit in time");
    } else {
        ESP_LOGI(TAG, "stream task exited");
    }
    send_json("{\"t\":\"stream_ack\",\"on\":false}");
}

// ---------- 拍照任务 ----------

void photo_task(void*)
{
    send_mem("photo_begin");
    uint8_t* data = nullptr;
    size_t len = 0;
    int w = 0;
    int h = 0;
    int enc_ms = 0;
    if (s_jpeg_buf == nullptr) {
        s_jpeg_buf = static_cast<uint8_t*>(heap_caps_malloc(kJpegCap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    }
    if (encode_frame(0, 0, 80, &data, &len, &w, &h, &enc_ms)) {
        char header[256];
        snprintf(header, sizeof(header),
                 "{\"t\":\"photo\",\"len\":%u,\"w\":%d,\"h\":%d,\"enc_ms\":%d}", (unsigned)len, w, h,
                 enc_ms);
        if (send_frame(header, data, len)) {
            send_json("{\"t\":\"photo_end\"}");
        } else {
            send_json("{\"t\":\"photo_fail\",\"err\":\"send\"}");
        }
    } else {
        send_json("{\"t\":\"photo_fail\",\"err\":\"encode\"}");
    }
    send_mem("photo_done");
    s_photo_running = false;
    vTaskDelete(nullptr);
}

// ---------- 命令处理 ----------

void handle_command(const char* data, size_t len)
{
    cJSON* root = cJSON_ParseWithLength(data, len);
    if (root == nullptr) {
        ESP_LOGW(TAG, "invalid json (%u bytes)", (unsigned)len);
        return;
    }

    const cJSON* type = cJSON_GetObjectItem(root, "t");
    if (cJSON_IsString(type)) {
        const char* cmd = type->valuestring;
        if (strcmp(cmd, "ping") == 0) {
            const cJSON* seq = cJSON_GetObjectItem(root, "seq");
            const cJSON* ts = cJSON_GetObjectItem(root, "ts");
            char buf[160];
            snprintf(buf, sizeof(buf), "{\"t\":\"pong\",\"seq\":%d,\"ts\":%.0f,\"rx_us\":%lld}",
                     seq ? seq->valueint : 0, ts ? ts->valuedouble : 0.0,
                     (long long)esp_timer_get_time());
            send_json(buf);
        } else if (strcmp(cmd, "ps") == 0) {
            const cJSON* mode = cJSON_GetObjectItem(root, "mode");
            const int m = mode ? mode->valueint : 1;
            const esp_err_t err = esp_wifi_set_ps(m ? WIFI_PS_MIN_MODEM : WIFI_PS_NONE);
            char buf[96];
            snprintf(buf, sizeof(buf), "{\"t\":\"ps_ack\",\"mode\":%d,\"err\":%d}", m, (int)err);
            send_json(buf);
        } else if (strcmp(cmd, "cfg") == 0) {
            const cJSON* fps = cJSON_GetObjectItem(root, "fps");
            const cJSON* q = cJSON_GetObjectItem(root, "q");
            const cJSON* w = cJSON_GetObjectItem(root, "w");
            const cJSON* h = cJSON_GetObjectItem(root, "h");
            const cJSON* chunk = cJSON_GetObjectItem(root, "chunk");
            if (cJSON_IsNumber(fps)) s_fps = std::clamp(fps->valueint, 0, 30);
            if (cJSON_IsNumber(q)) s_quality = std::clamp(q->valueint, 1, 100);
            if (cJSON_IsNumber(w)) s_req_w = std::clamp(w->valueint, 0, 1280);
            if (cJSON_IsNumber(h)) s_req_h = std::clamp(h->valueint, 0, 960);
            if (cJSON_IsNumber(chunk)) s_chunk = std::clamp(chunk->valueint, 512, 64 * 1024);
            char buf[160];
            snprintf(buf, sizeof(buf),
                     "{\"t\":\"cfg_ack\",\"fps\":%d,\"q\":%d,\"w\":%d,\"h\":%d,\"chunk\":%d}",
                     s_fps.load(), s_quality.load(), s_req_w.load(), s_req_h.load(), s_chunk.load());
            send_json(buf);
        } else if (strcmp(cmd, "stream") == 0) {
            const cJSON* on = cJSON_GetObjectItem(root, "on");
            if (cJSON_IsTrue(on)) {
                start_stream();
            } else {
                stop_stream();
            }
        } else if (strcmp(cmd, "photo") == 0) {
            if (!s_photo_running.exchange(true)) {
                TaskHandle_t task = nullptr;
                if (xTaskCreatePinnedToCore(photo_task, "spike_photo", 8192, nullptr, 4, &task, 1) !=
                        pdPASS ||
                    task == nullptr) {
                    s_photo_running = false;
                    send_json("{\"t\":\"photo_fail\",\"err\":\"task\"}");
                }
            }
        } else if (strcmp(cmd, "mem") == 0) {
            send_mem("ondemand");
        } else if (strcmp(cmd, "fov") == 0) {
            auto* cam = hal_bridge::board_get_camera();
            const cJSON* hflip = cJSON_GetObjectItem(root, "hflip");
            const cJSON* vflip = cJSON_GetObjectItem(root, "vflip");
            if (cam != nullptr && cJSON_IsBool(hflip)) {
                cam->SetHMirror(cJSON_IsTrue(hflip));
            }
            if (cam != nullptr && cJSON_IsBool(vflip)) {
                cam->SetVFlip(cJSON_IsTrue(vflip));
            }
            send_json("{\"t\":\"fov_ack\"}");
        }
    }

    cJSON_Delete(root);
}

// ---------- 连接管理 ----------

void send_hello()
{
    uint8_t mac[6] = {};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    const auto* desc = esp_app_get_description();
    char buf[256];
    snprintf(buf, sizeof(buf),
             "{\"t\":\"hello\",\"device_id\":\"%02x%02x%02x%02x%02x%02x\",\"fw\":\"%s\"}", mac[0],
             mac[1], mac[2], mac[3], mac[4], mac[5], desc ? desc->version : "unknown");
    send_json(buf);
}

void on_connected()
{
    ESP_LOGI(TAG, "connected");
    s_connected = true;
    s_hello_pending = true;
}

void on_disconnected()
{
    ESP_LOGW(TAG, "disconnected");
    s_connected = false;
    s_hello_pending = false;
}

void on_error(const NetworkError&)
{
    ESP_LOGW(TAG, "websocket error");
}

void on_data(const char* data, size_t len, bool binary)
{
    if (binary) {
        return;  // spike 不需要下行二进制
    }
    handle_command(data, len);
}

void network_task(void*)
{
    int backoff_ms = 2000;

    while (true) {
        if (!s_connected.load()) {
            {
                std::lock_guard<std::mutex> lock(s_ws_mutex);
                if (s_ws) {
                    s_ws->Close();
                    s_ws.reset();
                }
            }

            auto* network = Board::GetInstance().GetNetwork();
            if (network != nullptr) {
                auto ws = network->CreateWebSocket(2);
                if (ws) {
                    ws->SetReceiveBufferSize(1024);
                    ws->OnConnected(on_connected);
                    ws->OnDisconnected(on_disconnected);
                    ws->OnData(on_data);
                    ws->OnError(on_error);
                    {
                        std::lock_guard<std::mutex> lock(s_ws_mutex);
                        s_ws = std::move(ws);
                    }
                    ESP_LOGI(TAG, "connecting to %s", CONFIG_CHAN_SPIKE_WS_URL);
                    if (!s_ws->Connect(CONFIG_CHAN_SPIKE_WS_URL)) {
                        ESP_LOGW(TAG, "connect failed");
                        std::lock_guard<std::mutex> lock(s_ws_mutex);
                        s_ws.reset();
                    }
                }
            }

            if (!s_connected.load()) {
                vTaskDelay(pdMS_TO_TICKS(backoff_ms));
                backoff_ms = std::min(backoff_ms * 2, 30000);
            }
        } else {
            backoff_ms = 2000;
            if (s_hello_pending.exchange(false)) {
                send_hello();
            }
            vTaskDelay(pdMS_TO_TICKS(500));
        }
    }
}

}  // namespace

void spike_client_start()
{
    if (s_started.exchange(true)) {
        return;
    }
    ESP_LOGW(TAG, "spike client enabled, url=%s", CONFIG_CHAN_SPIKE_WS_URL);
    xTaskCreatePinnedToCore(network_task, "spike_net", 4096, nullptr, 3, nullptr, 0);
}

bool spike_client_connected()
{
    return s_connected.load();
}
