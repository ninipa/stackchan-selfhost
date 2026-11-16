// 遥控台（teleop）设备侧 P0：控制面消息 + 会话生命周期 + 视频推流 + 拍照。
//
// 设计要点（见 docs/teleop-evaluation-2026-11-12.md）：
//  - 运动执行复用官方 MOTION：GetStackChan().updateMotionFromJson()，
//    同时按官方做法在高频命令期间关闭 AutoAngleSync，2 秒无命令恢复；
//    再叠加 setModifyLock(true) 防止 idle 动作抢头（番茄钟同款）。
//  - Y 轴双端硬限 50–850（5°–85°，AGENTS §0.3），比驱动层 30–870 更紧。
//  - 视频走独立 WS（/device/video），与控制面隔离；帧 3fps、q50，
//    超 24KB 自动降质重编（借鉴官方 q20/10/5 阶梯）。
//  - 拍照用 StreamCaptures 手动丢 2 帧（避免 StackChanCamera::Capture 的
//    预览副作用），播快门音；q80 原分辨率（传感器原生 320×240）。
//  - 会话超时：10s 无报文 → 回中 + 解锁（舵机安全）；30s 无报文 → 关流退出（隐私）。

#include "teleop.h"

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
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>

#include <assets/assets.h>
#include <stackchan/stackchan.h>

#include "../board/hal_bridge.h"
#include "behavior_client.h"
#include "image_to_jpeg.h"
#include "pose.h"

#ifndef CONFIG_CHAN_TELEOP_VIDEO_WS_URL
#error "CONFIG_CHAN_TELEOP_VIDEO_WS_URL 未定义：先运行 firmware/tools/configure.sh"
#endif
static_assert(sizeof(CONFIG_CHAN_TELEOP_VIDEO_WS_URL) > 1,
              "CONFIG_CHAN_TELEOP_VIDEO_WS_URL 为空：先运行 firmware/tools/configure.sh");

#define TAG "Teleop"

namespace {

// ---------- 参数 ----------

constexpr int kYawLimit = 900;        // ±90°（0.1° 单位），舵机全行程 ±1280 但线缆不允许
constexpr int kPitchMin = 50;         // 5°（AGENTS §0.3 硬规则）
constexpr int kPitchMax = 850;        // 85°
constexpr int kPitchHome = chan_pose::kPitchHome;  // 20° 常态位（会话结束回这里）
constexpr int kPitchTeleopStart = kPitchMin;       // 遥控初始：5° ≈ 水平（用户反馈 20° 偏高）
constexpr uint32_t kSessionOrphanMs = 90000;       // 90s 无任何报文 → 视为 App 失联，关流退出
constexpr uint32_t kPostSessionHoldMs = 3000;      // 结束后保持常态位 3 秒再交回 idle
constexpr uint32_t kAutoSyncRestoreMs = 2000;      // 官方 AVATAR 同款
constexpr uint32_t kApplyMinIntervalMs = 100;      // 目标应用节流 10Hz

// 隐私指示：会话期间紫色（和语音绿/蓝区分）
// 左右各一颗（0/11），2026-11-16 灯序诊断确认这一对是对称的。
constexpr uint8_t kTeleopLedR = 150;
constexpr uint8_t kTeleopLedG = 40;
constexpr uint8_t kTeleopLedB = 255;

void set_privacy_leds(uint8_t r, uint8_t g, uint8_t b)
{
    GetHAL().setRgbColorPair(r, g, b);
    GetHAL().refreshRgb();
}

constexpr int kStreamFps = 3;
constexpr int kStreamQuality = 50;
constexpr size_t kChunk = 4096;
constexpr size_t kMaxFrameBytes = 24 * 1024;       // 超限降质重编（q50 → q35 → q20）
constexpr size_t kJpegCap = 256 * 1024;
constexpr int kPhotoQuality = 80;
constexpr int kScanMs = 5000;

constexpr uint32_t now_ms()
{
    return static_cast<uint32_t>(esp_timer_get_time() / 1000);
}

// ---------- 会话状态（主循环拥有） ----------

bool s_session = false;
bool s_streaming = false;
bool s_scanning = false;
bool s_auto_sync_off = false;
uint32_t s_last_apply_ms = 0;
uint32_t s_scan_start_ms = 0;
uint32_t s_last_led_ms = 0;
uint32_t s_post_hold_until = 0;  // 会话结束后保持常态位的截止时间

std::atomic<bool> s_pending_start{false};
std::atomic<bool> s_pending_end{false};
std::atomic<bool> s_pending_center{false};
std::atomic<bool> s_pending_scan{false};
std::atomic<bool> s_pending_photo{false};
std::atomic<bool> s_photo_busy{false};  // 拍照期间视频让路（不抢相机锁）
std::atomic<float> s_in_yaw_deg{0.0f};     // 绝对角度（度），来自 App
std::atomic<float> s_in_pitch_deg{5.0f};   // 5–85°
std::atomic<int> s_in_speed{600};
std::atomic<bool> s_target_dirty{false};
std::atomic<uint32_t> s_last_msg_ms{0};

uint8_t* s_jpeg_buf = nullptr;      // PSRAM
std::mutex s_encode_mutex;

// ---------- 视频通道 ----------

std::unique_ptr<WebSocket> s_ws;
std::mutex s_ws_mutex;
std::atomic<bool> s_connected{false};
std::atomic<bool> s_started{false};
std::atomic<bool> s_hello_pending{false};
std::atomic<uint32_t> s_seq{0};
std::mutex s_frame_mutex;  // 一帧（头 + 全部分块）整体发送

// 媒体资源（视频 WS + 网络任务）只在会话中占用；语音一响就释放，给 I2S 腾 DMA
std::atomic<TaskHandle_t> s_net_task{nullptr};
std::atomic<bool> s_net_stop{false};
std::atomic<bool> s_media_released{false};
uint32_t s_last_voice_ms = 0;
bool s_privacy_on = false;  // 紫灯当前是否点亮（主循环维护）

void network_task(void*);  // 定义在文件后部，media_acquire 需要

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

bool send_frame(const char* header, const uint8_t* data, size_t len)
{
    std::lock_guard<std::mutex> lock(s_frame_mutex);
    if (!send_json(header)) {
        return false;
    }
    for (size_t off = 0; off < len;) {
        const size_t n = std::min(kChunk, len - off);
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

bool encode_locked(const uint8_t* src, size_t src_len, int w, int h, v4l2_pix_fmt_t fmt,
                   int quality, size_t* out_len)
{
    JpegSink sink{s_jpeg_buf, kJpegCap, 0, false};
    const bool ok = image_to_jpeg_cb(const_cast<uint8_t*>(src), src_len, static_cast<uint16_t>(w),
                                     static_cast<uint16_t>(h), fmt, static_cast<uint8_t>(quality),
                                     jpeg_sink_cb, &sink);
    if (!ok || !sink.valid || sink.len == 0) {
        return false;
    }
    *out_len = sink.len;
    return true;
}

/** 抓一帧（丢弃 warmup 帧）并编码；超 24KB 自动降质重编。retry_ms>0 时等相机锁。 */
bool capture_and_encode(int warmup, size_t* out_len, int* out_w, int* out_h, int* out_quality,
                        int* enc_ms, int retry_ms = 0)
{
    auto* cam = hal_bridge::board_get_camera();
    if (cam == nullptr || s_jpeg_buf == nullptr) {
        ESP_LOGW(TAG, "capture: cam=%p buf=%p", cam, s_jpeg_buf);
        return false;
    }

    std::lock_guard<std::mutex> enc_lock(s_encode_mutex);

    std::unique_lock<std::recursive_mutex> frame_lock;
    const uint32_t lock_deadline = now_ms() + (uint32_t)retry_ms;
    while (true) {
        frame_lock = cam->TryLockFrame();
        if (frame_lock.owns_lock()) {
            break;
        }
        if (now_ms() >= lock_deadline) {
            if (retry_ms > 0) {
                ESP_LOGW(TAG, "capture: camera busy after %dms", retry_ms);
            }
            return false;  // AI 拍照占用相机 → 跳过本帧（官方 CAMERA 同款）
        }
        vTaskDelay(pdMS_TO_TICKS(30));
    }

    for (int i = 0; i <= warmup; ++i) {
        if (!cam->StreamCaptures()) {
            ESP_LOGW(TAG, "capture: StreamCaptures failed at %d", i);
            return false;
        }
    }

    const uint8_t* src = cam->GetFrameData();
    const size_t src_len = cam->GetFrameSize();
    const int w = cam->GetFrameWidth();
    const int h = cam->GetFrameHeight();
    const auto fmt = cam->GetFrameFormat();
    if (src == nullptr || src_len == 0 || w <= 0 || h <= 0) {
        return false;
    }

    const uint32_t t0 = esp_timer_get_time();
    int quality = kStreamQuality;
    size_t len = 0;
    while (!encode_locked(src, src_len, w, h, fmt, quality, &len)) {
        return false;
    }
    if (len > kMaxFrameBytes) {
        const int fallback[] = {35, 20};
        for (const int q : fallback) {
            if (quality <= q) {
                continue;
            }
            size_t alt = 0;
            if (encode_locked(src, src_len, w, h, fmt, q, &alt) && alt < len) {
                quality = q;
                len = alt;
                if (len <= kMaxFrameBytes) {
                    break;
                }
            }
        }
        if (len > kMaxFrameBytes) {
            ESP_LOGW(TAG, "frame still %zu bytes after quality fallback", len);
        }
    }
    const uint32_t t1 = esp_timer_get_time();

    *out_len = len;
    *out_w = w;
    *out_h = h;
    *out_quality = quality;
    *enc_ms = static_cast<int>((t1 - t0) / 1000);
    return true;
}

// ---------- 视频推流（主循环驱动，不占额外任务栈） ----------

uint32_t s_next_frame_ms = 0;

/**
 * 每帧在主循环里执行一次采集+编码+发送（约 50ms）。
 * 不再用独立任务：语音启动需要 DMA 内存时，省下的 6–8KB 任务栈就是保命的。
 * 官方 CAMERA 也是在自己的 update() 里干这事。
 */
void pump_stream(uint32_t now)
{
    if (!s_connected.load() || !s_streaming || s_photo_busy.load()) {
        return;
    }
    if (now < s_next_frame_ms) {
        return;
    }

    const uint32_t t0 = now_ms();
    size_t len = 0;
    int w = 0;
    int h = 0;
    int quality = 0;
    int enc_ms = 0;
    if (capture_and_encode(0, &len, &w, &h, &quality, &enc_ms)) {
        char header[320];
        snprintf(header, sizeof(header),
                 "{\"t\":\"frame\",\"seq\":%u,\"len\":%u,\"w\":%d,\"h\":%d,\"q\":%d,"
                 "\"enc_ms\":%d,\"int_largest\":%u,\"dma_largest\":%u}",
                 static_cast<unsigned>(++s_seq), static_cast<unsigned>(len), w, h, quality, enc_ms,
                 static_cast<unsigned>(
                     heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
                 static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_DMA)));
        if (!send_frame(header, s_jpeg_buf, len)) {
            ESP_LOGW(TAG, "frame send failed; pausing stream");
            s_streaming = false;
        }
    }
    // 失败（相机忙/编码失败）就跳过本帧，下一拍再试
    s_next_frame_ms = t0 + 1000 / kStreamFps;
}

void photo_task(void*)
{
    s_photo_busy = true;
    ESP_LOGI(TAG, "photo start");
    size_t len = 0;
    int w = 0;
    int h = 0;
    int quality = 0;
    int enc_ms = 0;

    if (s_jpeg_buf == nullptr) {
        s_jpeg_buf = static_cast<uint8_t*>(
            heap_caps_malloc(kJpegCap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    }
    // 快门音（官方 StackChanCamera::Capture 同款素材）
    hal_bridge::app_play_sound(OGG_CAMERA_SHUTTER);
    bool ok = false;
    if (capture_and_encode(2, &len, &w, &h, &quality, &enc_ms, 1500)) {
        char header[256];
        snprintf(header, sizeof(header),
                 "{\"t\":\"photo\",\"len\":%u,\"w\":%d,\"h\":%d,\"enc_ms\":%d}",
                 static_cast<unsigned>(len), w, h, enc_ms);
        if (send_frame(header, s_jpeg_buf, len)) {
            send_json("{\"t\":\"photo_end\"}");
            ok = true;
        } else {
            send_json("{\"t\":\"photo_fail\",\"err\":\"send\"}");
        }
    } else {
        send_json("{\"t\":\"photo_fail\",\"err\":\"encode\"}");
    }
    ESP_LOGI(TAG, "photo done ok=%d len=%u w=%d h=%d", (int)ok, (unsigned)len, w, h);
    s_photo_busy = false;
    s_pending_photo = false;
    vTaskDelete(nullptr);
}

// ---------- 运动 ----------

void apply_pose(int yaw, int pitch, int speed)
{
    yaw = std::clamp(yaw, -kYawLimit, kYawLimit);
    pitch = std::clamp(pitch, kPitchMin, kPitchMax);
    speed = std::clamp(speed, 100, 1000);
    char json[192];
    snprintf(json, sizeof(json),
             "{\"yawServo\":{\"angle\":%d,\"speed\":%d},\"pitchServo\":{\"angle\":%d,\"speed\":%d}}",
             yaw, speed, pitch, speed);
    GetStackChan().updateMotionFromJson(json);
}

/** 会话结束回常态位（20°） */
void apply_home(int speed)
{
    apply_pose(0, kPitchHome, speed);
}

/** 遥控初始/回正位：水平 5°（用户反馈 20° 初始偏高） */
void apply_teleop_start(int speed)
{
    apply_pose(0, kPitchTeleopStart, speed);
}

/** 绝对角度控制（度）：yaw ±90°，pitch 5–85°。App 直接给角度，避免归一化映射歧义。 */
void apply_degrees(float yaw_deg, float pitch_deg, int speed)
{
    const int yaw = static_cast<int>(std::lround(yaw_deg)) * 10;   // 度 → 0.1°
    const int pitch = static_cast<int>(std::lround(pitch_deg)) * 10;
    apply_pose(yaw, pitch, speed);
}

void ensure_locked()
{
    auto& motion = GetStackChan().motion();
    motion.setModifyLock(true);
    if (!s_auto_sync_off) {
        motion.setAutoAngleSyncEnabled(false);
        s_auto_sync_off = true;
    }
}

// ---------- 媒体资源获取/释放 ----------
//
// 2026-11-12 崩溃根因：teleop 的视频 WS + 任务常驻占用内部 RAM，
// 点屏幕进入 listening 时 I2S 重新分配 DMA 描述符失败 → 空指针 panic。
// 因此：视频 WS/网络任务只在会话中建立；语音活动一出现立即释放。

void media_acquire()
{
    if (s_net_task.load() != nullptr) {
        s_streaming = true;
        return;
    }
    s_net_stop = false;
    s_hello_pending = false;
    s_next_frame_ms = 0;
    TaskHandle_t task = nullptr;
    // 栈从 4096 降到 3072；视频采集已改为主循环，不再占 8KB 任务栈
    if (xTaskCreatePinnedToCore(network_task, "teleop_net", 3072, nullptr, 3, &task, 0) != pdPASS) {
        ESP_LOGE(TAG, "create teleop_net failed");
        s_streaming = false;
        return;
    }
    s_net_task = task;
    s_streaming = true;
    s_media_released = false;
    ESP_LOGI(TAG, "media acquire (free sram=%u, dma largest=%u)",
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
             static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_DMA)));
}

void media_release(const char* reason)
{
    if (s_net_task.load() == nullptr) {
        s_streaming = false;
        return;
    }
    s_streaming = false;
    s_net_stop = true;
    {
        std::lock_guard<std::mutex> lock(s_ws_mutex);
        if (s_ws) {
            s_ws->Close();  // 立即释放 TCP/lwIP 缓冲；对象由网络任务退出时 reset
        }
    }
    TaskHandle_t task = s_net_task.load();
    if (task != nullptr) {
        xTaskNotifyGive(task);
    }
    for (int i = 0; i < 40 && s_net_task.load() != nullptr; ++i) {
        vTaskDelay(pdMS_TO_TICKS(5));
    }
    s_connected = false;
    ESP_LOGI(TAG, "media release (%s), free sram=%u, dma largest=%u, net=%s", reason,
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
             static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_DMA)),
             s_net_task.load() != nullptr ? "alive" : "gone");
}

void session_start()
{
    if (s_session) {
        return;
    }
    if (!hal_bridge::is_xiaozhi_idle()) {
        ESP_LOGW(TAG, "start ignored: xiaozhi busy");
        return;
    }
    if (s_jpeg_buf == nullptr) {
        s_jpeg_buf = static_cast<uint8_t*>(
            heap_caps_malloc(kJpegCap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    }
    if (s_jpeg_buf == nullptr) {
        ESP_LOGE(TAG, "alloc jpeg buffer failed");
        return;
    }

    s_session = true;
    s_streaming = true;
    s_scanning = false;
    s_post_hold_until = 0;
    s_last_msg_ms = now_ms();
    media_acquire();
    ensure_locked();
    apply_teleop_start(500);
    // 隐私指示：摄像头工作中，左右各一颗紫色（0/11）
    set_privacy_leds(kTeleopLedR, kTeleopLedG, kTeleopLedB);
    s_privacy_on = true;
    s_last_led_ms = now_ms();

    const auto* desc = esp_app_get_description();
    ESP_LOGI(TAG, "session start (fw=%s)", desc ? desc->version : "?");
}

void session_end(const char* reason)
{
    if (!s_session) {
        return;
    }
    s_session = false;
    s_streaming = false;
    s_scanning = false;
    apply_home(500);
    auto& motion = GetStackChan().motion();
    motion.setAutoAngleSyncEnabled(true);
    // 结束后先保持常态位 3 秒，避免 idle_motion 立刻把角度带到随机位置
    // （用户反馈：遥控一关，有时还停在 5°，有时又比 20° 还大——后者就是 idle 的随机俯仰）
    motion.setModifyLock(true);
    s_post_hold_until = now_ms() + kPostSessionHoldMs;
    s_auto_sync_off = false;
    // 关掉隐私指示（0/11；语音状态灯结束时会自行刷黑/刷色）
    set_privacy_leds(0, 0, 0);
    s_privacy_on = false;
    // 会话结束立即释放视频 WS 任务，回到"零常驻"
    media_release("session_end");
    s_media_released = false;
    ESP_LOGI(TAG, "session end (%s)", reason);
}

// ---------- 连接 ----------

void send_hello()
{
    uint8_t mac[6] = {};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    char buf[160];
    snprintf(buf, sizeof(buf), "{\"t\":\"hello\",\"device_id\":\"%02x%02x%02x%02x%02x%02x\"}",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    send_json(buf);
}

void on_connected()
{
    ESP_LOGI(TAG, "video channel connected");
    s_connected = true;
    s_hello_pending = true;
}

void on_disconnected()
{
    ESP_LOGW(TAG, "video channel disconnected");
    s_connected = false;
    s_streaming = false;
}

void on_error(const NetworkError&)
{
    ESP_LOGW(TAG, "video channel error");
}

void network_task(void*)
{
    int backoff_ms = 2000;
    while (!s_net_stop.load()) {
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
                    ws->OnError(on_error);
                    ws->OnData([](const char* data, size_t len, bool binary) {
                        // 诊断用 ping：服务端发 {"t":"ping","seq","ts"}，原样回声 ts
                        if (binary || data == nullptr) {
                            return;
                        }
                        cJSON* root = cJSON_ParseWithLength(data, len);
                        if (root == nullptr) {
                            return;
                        }
                        const cJSON* t = cJSON_GetObjectItem(root, "t");
                        if (cJSON_IsString(t) && strcmp(t->valuestring, "ping") == 0) {
                            const cJSON* seq = cJSON_GetObjectItem(root, "seq");
                            const cJSON* ts = cJSON_GetObjectItem(root, "ts");
                            const int64_t rx_us = esp_timer_get_time();
                            wifi_ps_type_t ps = WIFI_PS_NONE;
                            esp_wifi_get_ps(&ps);
                            char buf[224];
                            snprintf(buf, sizeof(buf),
                                     "{\"t\":\"pong\",\"seq\":%d,\"ts\":%.0f,\"hold_us\":%lld,"
                                     "\"ps\":%d}",
                                     seq ? seq->valueint : 0, ts ? ts->valuedouble : 0.0,
                                     (long long)(esp_timer_get_time() - rx_us), (int)ps);
                            send_json(buf);
                            ESP_LOGI(TAG, "ping seq=%d hold=%lldus ps=%d", seq ? seq->valueint : 0,
                                     (long long)(esp_timer_get_time() - rx_us), (int)ps);
                        }
                        cJSON_Delete(root);
                    });
                    {
                        std::lock_guard<std::mutex> lock(s_ws_mutex);
                        s_ws = std::move(ws);
                    }
                    if (!s_ws->Connect(CONFIG_CHAN_TELEOP_VIDEO_WS_URL)) {
                        std::lock_guard<std::mutex> lock(s_ws_mutex);
                        s_ws.reset();
                    }
                }
            }
            if (!s_connected.load() && !s_net_stop.load()) {
                // 可被 media_release 用 notify 立即唤醒
                ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(backoff_ms));
                backoff_ms = std::min(backoff_ms * 2, 30000);
            }
        } else {
            backoff_ms = 2000;
            if (s_hello_pending.exchange(false)) {
                send_hello();
            }
            ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(500));
        }
    }

    // 退出清理：关闭并释放 WS（此时不会再有 Send/Connect 调用者）
    {
        std::lock_guard<std::mutex> lock(s_ws_mutex);
        if (s_ws) {
            s_ws->Close();
            s_ws.reset();
        }
    }
    s_connected = false;
    s_net_task = nullptr;
    ESP_LOGI(TAG, "teleop_net exited");
    vTaskDelete(nullptr);
}

}  // namespace

// ---------- 对外接口 ----------

void teleop_start()
{
    if (s_started.exchange(true)) {
        return;
    }
    // 懒加载：开机不建 WS/任务，第一次开遥控会话时才占用媒体资源
    ESP_LOGI(TAG, "teleop registered (lazy media), video url=%s", CONFIG_CHAN_TELEOP_VIDEO_WS_URL);
}

void teleop_update()
{    const uint32_t now = now_ms();

    // start/end 只在这里执行：运动 API 不能从 WS 回调线程调用
    if (s_pending_start.exchange(false)) {
        session_start();
    }
    if (s_pending_end.exchange(false)) {
        session_end("command");
    }

    // 会话结束后的 3 秒常态位保持
    if (!s_session && s_post_hold_until != 0 && now >= s_post_hold_until) {
        GetStackChan().motion().setModifyLock(false);
        s_post_hold_until = 0;
    }

    if (!s_session) {
        return;
    }

    // 语音释放媒体后，等语音结束 800ms 再恢复推流
    if (s_media_released && hal_bridge::is_xiaozhi_idle() && (now - s_last_voice_ms) > 800) {
        media_acquire();
        s_media_released = false;
    }

    // 推流在主循环里做（不占独立任务栈）；语音占用媒体时自动跳过
    pump_stream(now);

    const uint32_t last = s_last_msg_ms.load();
    // 注意：last 由控制 WS 回调线程更新，可能比本函数开头捕获的 now 更新。
    // 直接用 now - last 会无符号下溢（实测 now=14927 / last=14939 → diff=42 亿 → 误判 orphan）。
    const uint32_t now_check = now_ms();
    const uint32_t idle_ms = (now_check > last) ? (now_check - last) : 0;
    const bool voice_busy = !hal_bridge::is_xiaozhi_idle();

    // 隐私灯与对话灯互斥：语音期间**只停止校正、不主动写灯**——
    // 状态灯会把整条灯带刷成绿/蓝，主动清反而会擦掉它。
    // 语音结束（回到 standby）后灯带被刷黑，这里再恢复紫色。
    if (voice_busy) {
        s_privacy_on = false;
    } else if (!s_privacy_on) {
        set_privacy_leds(kTeleopLedR, kTeleopLedG, kTeleopLedB);
        s_privacy_on = true;
        s_last_led_ms = now;
    } else if ((now - s_last_led_ms) >= 1000) {
        set_privacy_leds(kTeleopLedR, kTeleopLedG, kTeleopLedB);
        s_last_led_ms = now;
    }

    // 环视扫描：±90° 往返一次，结束回遥控初始位
    if (s_pending_scan.exchange(false) && !s_scanning) {
        s_scanning = true;
        s_scan_start_ms = now;
        ensure_locked();
    }
    if (s_scanning) {
        const float p = static_cast<float>(now - s_scan_start_ms) / kScanMs;
        if (p >= 1.0f) {
            s_scanning = false;
            apply_teleop_start(400);
        } else {
            // 水平往返扫：pitch 保持 5°
            apply_degrees(-90.0f + 180.0f * p, static_cast<float>(kPitchTeleopStart) / 10.0f, 500);
        }
        s_last_apply_ms = now;
        return;
    }

    if (s_pending_center.exchange(false)) {
        ensure_locked();
        apply_teleop_start(500);
        s_last_apply_ms = now;
    }

    // 语音优先：说话/听音期间不驱动舵机（会话和流保持）
    if (!voice_busy && s_target_dirty.load() && (now - s_last_apply_ms) >= kApplyMinIntervalMs) {
        s_target_dirty = false;
        ensure_locked();
        apply_degrees(s_in_yaw_deg.load(), s_in_pitch_deg.load(), s_in_speed.load());
        s_last_apply_ms = now;
    }

    // AutoAngleSync：2 秒无命令后恢复（官方 AVATAR 同款，只影响动画起点的同步方式）
    if (s_auto_sync_off && idle_ms > kAutoSyncRestoreMs) {
        GetStackChan().motion().setAutoAngleSyncEnabled(true);
        s_auto_sync_off = false;
    }

    // 会话中**不自动回 home**；只有 App 失联 90 秒（停止 keepalive）才结束会话回常态位。
    if (idle_ms > kSessionOrphanMs) {
        session_end("orphan_timeout");
    }
}

void teleop_on_voice_activity()
{
    if (!s_session) {
        return;
    }
    // 点屏幕/说话会立刻触发：马上释放视频 WS，把内部 RAM/DMA 让给 I2S
    // （2026-11-12 崩溃根因：语音启动时 I2S DMA 描述符分配失败 → LoadProhibited）
    // 紫灯不在这里写：紧接着状态灯会把整条灯带刷成绿/蓝，主动清会擦掉它。
    s_last_voice_ms = now_ms();
    s_media_released = true;
    s_privacy_on = false;
    media_release("voice_activity");
}

void teleop_handle_control(cJSON* root)
{
    if (root == nullptr) {
        return;
    }

    const cJSON* start = cJSON_GetObjectItem(root, "start");
    const cJSON* end = cJSON_GetObjectItem(root, "end");
    const cJSON* center = cJSON_GetObjectItem(root, "center");
    const cJSON* scan = cJSON_GetObjectItem(root, "scan");
    const cJSON* yaw_deg = cJSON_GetObjectItem(root, "yaw");    // 单位：度（±90）
    const cJSON* pitch_deg = cJSON_GetObjectItem(root, "pitch");  // 单位：度（5–85）
    const cJSON* speed = cJSON_GetObjectItem(root, "speed");

    s_last_msg_ms = now_ms();

    if (cJSON_IsTrue(start)) {
        s_pending_start = true;  // 实际执行在主循环（运动 API 线程约束）
        return;
    }
    if (cJSON_IsTrue(end)) {
        s_pending_end = true;
        return;
    }
    if (cJSON_IsTrue(center)) {
        s_pending_center = true;
    }
    if (cJSON_IsTrue(scan)) {
        s_pending_scan = true;
    }
    if (cJSON_IsNumber(yaw_deg) && cJSON_IsNumber(pitch_deg)) {
        if (!s_session) {
            return;  // 没有 start 就不接受移动
        }
        s_in_yaw_deg = std::clamp(static_cast<float>(yaw_deg->valuedouble), -90.0f, 90.0f);
        s_in_pitch_deg = std::clamp(static_cast<float>(pitch_deg->valuedouble), 5.0f, 85.0f);
        if (cJSON_IsNumber(speed)) {
            s_in_speed = std::clamp(speed->valueint, 100, 1000);
        }
        s_target_dirty = true;
    }
}

void teleop_request_photo()
{
    if (s_pending_photo.exchange(true)) {
        return;
    }
    if (s_jpeg_buf == nullptr) {
        s_jpeg_buf = static_cast<uint8_t*>(
            heap_caps_malloc(kJpegCap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    }
    // 栈不能超过当前内部 RAM 最大连续块（视频模块常驻后只剩 ~7.6KB），
    // 早先 8192 会 xTaskCreate 失败、拍照静默 504。
    if (xTaskCreatePinnedToCore(photo_task, "teleop_photo", 6144, nullptr, 4, nullptr, 1) != pdPASS) {
        s_pending_photo = false;
        send_json("{\"t\":\"photo_fail\",\"err\":\"task\"}");
    }
}
