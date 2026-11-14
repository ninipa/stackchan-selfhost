#include "behavior_runtime.h"

#include <application.h>
#include <audio/audio_service.h>
#include <board.h>
#include <cJSON.h>
#include <display.h>
#include <esp_app_desc.h>
#include <esp_heap_caps.h>
#include <esp_http_client.h>
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <hal/hal.h>
#include <stackchan/stackchan.h>

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
#include <string_view>
#include <vector>

#include "behavior_client.h"
#include "behavior_audio.h"
#include "pomodoro.h"
#include "teleop.h"

#define TAG "Behavior"

namespace {

enum class Op {
    Emotion,
    Led,
    Move,
    Wait,
    Say,
};

struct Step {
    Op op = Op::Wait;
    std::string emotion;
    std::string text;
    std::string audio;
    std::string audio_id;
    int audio_len = 0;
    int r = 0;
    int g = 0;
    int b = 0;
    int yaw = 0;
    int pitch = 0;
    int speed = 300;
    int ms = 0;
    int repeat = 1;
    int gap_ms = 0;
};

struct Job {
    std::string id;
    std::vector<Step> steps;
    size_t index = 0;
    int say_phase = 0;  // 0=start, 1=playing, 2=gap
    int repeat_left = 0;
    uint8_t* audio_buf = nullptr;
    size_t audio_len = 0;
    uint32_t wait_until = 0;
    uint32_t deadline = 0;
    uint32_t step_started_at = 0;
    bool move_started = false;
};

QueueHandle_t s_queue = nullptr;
Job s_job;
Job s_pending;
bool s_active = false;
bool s_has_pending = false;
std::atomic<bool> s_voice_activity{false};

const char* jstr(const cJSON* obj, const char* key)
{
    const cJSON* item = cJSON_GetObjectItem(obj, key);
    return cJSON_IsString(item) ? item->valuestring : nullptr;
}

int jint(const cJSON* obj, const char* key, int fallback)
{
    const cJSON* item = cJSON_GetObjectItem(obj, key);
    return cJSON_IsNumber(item) ? item->valueint : fallback;
}

bool parse_script(const std::string& json, Job& job)
{
    cJSON* root = cJSON_Parse(json.c_str());
    if (root == nullptr) {
        return false;
    }

    const cJSON* id = cJSON_GetObjectItem(root, "job_id");
    if (!cJSON_IsString(id) || id->valuestring[0] == '\0') {
        cJSON_Delete(root);
        return false;
    }
    job.id = id->valuestring;

    const cJSON* steps = cJSON_GetObjectItem(root, "steps");
    if (!cJSON_IsArray(steps)) {
        cJSON_Delete(root);
        return false;
    }

    cJSON* item = nullptr;
    cJSON_ArrayForEach(item, steps)
    {
        const cJSON* op = cJSON_GetObjectItem(item, "op");
        if (!cJSON_IsString(op)) {
            continue;
        }

        Step step;
        const std::string name = op->valuestring;
        if (name == "emotion") {
            step.op = Op::Emotion;
            const char* emotion = jstr(item, "emotion");
            step.emotion = emotion ? emotion : "neutral";
        } else if (name == "led") {
            step.op = Op::Led;
            step.r = std::clamp(jint(item, "r", 0), 0, 255);
            step.g = std::clamp(jint(item, "g", 0), 0, 255);
            step.b = std::clamp(jint(item, "b", 0), 0, 255);
        } else if (name == "move") {
            step.op = Op::Move;
            step.yaw = std::clamp(jint(item, "yaw", 0), -1280, 1280);
            step.pitch = std::clamp(jint(item, "pitch", 0), 30, 870);
            step.speed = std::clamp(jint(item, "speed", 300), 0, 1000);
        } else if (name == "wait") {
            step.op = Op::Wait;
            step.ms = std::clamp(jint(item, "ms", 0), 0, 60000);
        } else if (name == "say") {
            step.op = Op::Say;
            const char* audio = jstr(item, "audio");
            const char* audio_id = jstr(item, "audio_id");
            if (audio == nullptr && audio_id == nullptr) {
                continue;
            }
            step.audio = audio ? audio : "";
            step.audio_id = audio_id ? audio_id : "";
            step.audio_len = jint(item, "audio_len", 0);
            const char* text = jstr(item, "text");
            step.text = text ? text : "";
            step.repeat = std::clamp(jint(item, "repeat", 1), 1, 20);
            step.gap_ms = std::clamp(jint(item, "gap_ms", 0), 0, 10000);
        } else {
            continue;
        }
        job.steps.push_back(std::move(step));
    }

    cJSON_Delete(root);
    return !job.steps.empty();
}

Display* display()
{
    return Board::GetInstance().GetDisplay();
}

AudioService* audio()
{
    return &Application::GetInstance().GetAudioService();
}

void free_audio(Job& job)
{
    if (job.audio_buf != nullptr) {
        heap_caps_free(job.audio_buf);
        job.audio_buf = nullptr;
        job.audio_len = 0;
    }
}

void send_event(const char* type, const std::string& job_id, const char* error = nullptr)
{
    cJSON* root = cJSON_CreateObject();
    if (root == nullptr) {
        return;
    }
    cJSON_AddStringToObject(root, "type", type);
    cJSON_AddStringToObject(root, "job_id", job_id.c_str());
    if (error != nullptr) {
        cJSON_AddStringToObject(root, "error", error);
    }
    char* text = cJSON_PrintUnformatted(root);
    if (text != nullptr) {
        behavior_client_send_json(text);
        cJSON_free(text);
    }
    cJSON_Delete(root);
}

void cancel_job(const char* reason)
{
    if (!s_active) {
        return;
    }
    ESP_LOGW(TAG, "job %s cancelled: %s", s_job.id.c_str(), reason);
    send_event("job_error", s_job.id, reason);
    if (display() != nullptr) {
        display()->ClearChatMessages();
    }
    free_audio(s_job);
    behavior_audio_clear();
    s_job = Job();
    s_active = false;
}

void advance()
{
    free_audio(s_job);
    s_job.index++;
    s_job.say_phase = 0;
    s_job.repeat_left = 0;
    s_job.wait_until = 0;
    s_job.deadline = 0;
    s_job.step_started_at = 0;
    s_job.move_started = false;

    if (s_job.index >= s_job.steps.size()) {
        ESP_LOGI(TAG, "job %s done", s_job.id.c_str());
        send_event("job_done", s_job.id);
        if (display() != nullptr) {
            display()->ClearChatMessages();
        }
        behavior_audio_clear();
        s_job = Job();
        s_active = false;
    }
}

void start_job(Job&& job)
{
    s_job = std::move(job);
    s_active = true;
    s_job.index = 0;
    send_event("job_started", s_job.id);
    ESP_LOGI(TAG, "job %s started (%u steps)", s_job.id.c_str(),
             static_cast<unsigned>(s_job.steps.size()));
}

bool download_ogg(const std::string& url, uint8_t** out_buf, size_t* out_len)
{
    *out_buf = nullptr;
    *out_len = 0;

    esp_http_client_config_t config = {};
    config.url = url.c_str();
    config.timeout_ms = 10000;
    // 内部 DMA 很紧（最大连续块 ~1.4KB），4KB 缓冲会分配失败；
    // 512B 足够放响应头，body 走 read 循环进 PSRAM。
    config.buffer_size = 512;
    config.buffer_size_tx = 512;

    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == nullptr) {
        ESP_LOGW(TAG, "http client init failed");
        return false;
    }

    constexpr size_t kMaxAudio = 2 * 1024 * 1024;
    bool ok = false;

    if (esp_http_client_open(client, 0) == ESP_OK) {
        const int64_t content_len = esp_http_client_fetch_headers(client);
        const size_t cap = (content_len > 0 && static_cast<uint64_t>(content_len) <= kMaxAudio)
                               ? static_cast<size_t>(content_len)
                               : kMaxAudio;

        auto* buf = static_cast<uint8_t*>(heap_caps_malloc(cap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
        if (buf == nullptr) {
            buf = static_cast<uint8_t*>(heap_caps_malloc(cap, MALLOC_CAP_8BIT));
        }

        if (buf != nullptr) {
            size_t total = 0;
            while (total < cap) {
                const int read = esp_http_client_read(client, reinterpret_cast<char*>(buf) + total,
                                                      static_cast<int>(cap - total));
                if (read <= 0) {
                    break;
                }
                total += static_cast<size_t>(read);
            }

            if (total > 0 && (content_len <= 0 || total == static_cast<size_t>(content_len))) {
                *out_buf = buf;
                *out_len = total;
                ok = true;
            } else {
                heap_caps_free(buf);
                ESP_LOGW(TAG, "audio size mismatch: got %u / %lld",
                         static_cast<unsigned>(total), static_cast<long long>(content_len));
            }
        }
    } else {
        ESP_LOGW(TAG, "http open failed: %s errno=%d", url.c_str(), esp_http_client_get_errno(client));
    }

    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return ok;
}

void handle_say(const Step& step, uint32_t now)
{
    if (s_job.say_phase == 0) {
        if (s_job.audio_buf == nullptr) {
            if (!step.audio_id.empty()) {
                if (!behavior_audio_take(step.audio_id.c_str(), &s_job.audio_buf, &s_job.audio_len)) {
                    ESP_LOGW(TAG, "audio asset missing: %s", step.audio_id.c_str());
                    cancel_job("audio_missing");
                    return;
                }
            } else if (!download_ogg(step.audio, &s_job.audio_buf, &s_job.audio_len)) {
                cancel_job("audio_download_failed");
                return;
            }
            s_job.repeat_left = std::max(1, step.repeat);
        }

        if (display() != nullptr) {
            // 气泡在底部，会被番茄钟信息带盖住：先说一句让位
            pomodoro_yield_ms(4000);
            display()->SetChatMessage("assistant", step.text.c_str());
        }
        audio()->PlaySound(std::string_view(reinterpret_cast<const char*>(s_job.audio_buf), s_job.audio_len));
        s_job.say_phase = 1;
        s_job.step_started_at = now;
        s_job.deadline = now + 30000;
        return;
    }

    if (s_job.say_phase == 1) {
        if (now - s_job.step_started_at < 300) {
            return;
        }
        if (audio()->IsPlaybackIdle()) {
            s_job.repeat_left--;
            if (s_job.repeat_left > 0) {
                s_job.say_phase = 2;
                s_job.wait_until = now + std::max(0, step.gap_ms);
            } else {
                advance();
            }
        } else if (now > s_job.deadline) {
            cancel_job("audio_timeout");
        }
        return;
    }

    if (s_job.say_phase == 2 && now >= s_job.wait_until) {
        audio()->PlaySound(std::string_view(reinterpret_cast<const char*>(s_job.audio_buf), s_job.audio_len));
        s_job.say_phase = 1;
        s_job.step_started_at = now;
        s_job.deadline = now + 30000;
    }
}

void update_step()
{
    if (!s_active) {
        return;
    }

    const uint32_t now = GetHAL().millis();
    if (s_job.index >= s_job.steps.size()) {
        advance();
        return;
    }

    const Step& step = s_job.steps[s_job.index];
    switch (step.op) {
        case Op::Emotion:
            if (display() != nullptr) {
                display()->SetEmotion(step.emotion.c_str());
            }
            advance();
            break;

        case Op::Led:
            GetHAL().setRgbColor(0, static_cast<uint8_t>(step.r), static_cast<uint8_t>(step.g),
                                 static_cast<uint8_t>(step.b));
            GetHAL().refreshRgb();
            advance();
            break;

        case Op::Move: {
            auto& motion = GetStackChan().motion();
            if (!s_job.move_started) {
                // 打点：以后排查"动作慢/没到位"不用再猜，串口直接有目标角度
                ESP_LOGI(TAG, "move yaw=%d pitch=%d speed=%d", step.yaw, step.pitch, step.speed);
                motion.moveWithSpeed(step.yaw, step.pitch, step.speed);
                s_job.move_started = true;
                s_job.step_started_at = now;
                s_job.deadline = now + 3000;
            } else if (now - s_job.step_started_at >= 200 && (!motion.isMoving() || now > s_job.deadline)) {
                advance();
            }
            break;
        }

        case Op::Wait:
            if (s_job.wait_until == 0) {
                s_job.wait_until = now + step.ms;
            }
            if (now >= s_job.wait_until) {
                advance();
            }
            break;

        case Op::Say:
            handle_say(step, now);
            break;
    }
}

void enqueue(const char* prefix, const std::string& payload)
{
    if (s_queue == nullptr) {
        return;
    }
    char* item = static_cast<char*>(malloc(payload.size() + 2));
    if (item == nullptr) {
        return;
    }
    item[0] = prefix[0];
    memcpy(item + 1, payload.c_str(), payload.size() + 1);
    if (xQueueSend(s_queue, &item, 0) != pdTRUE) {
        ESP_LOGW(TAG, "queue full, drop message");
        free(item);
    }
}

}  // namespace

void behavior_runtime_init()
{
    if (s_queue == nullptr) {
        s_queue = xQueueCreate(8, sizeof(char*));
    }
}

void behavior_runtime_enqueue_script(const char* json)
{
    enqueue("B", json ? json : "");
}

void behavior_runtime_enqueue_timer(const char* json)
{
    enqueue("T", json ? json : "");
}

void behavior_runtime_enqueue_timer_cancel(const char* job_id)
{
    enqueue("X", job_id ? job_id : "");
}

void behavior_runtime_enqueue_panel()
{
    // 调试/自测用：等价于"远程长按"，让设备把番茄钟抽屉打开，方便截图验证布局
    enqueue("P", "");
}

void behavior_runtime_enqueue_timer_complete(const char* job_id)
{
    // 'C' 已经被 message job 的取消占用，番茄钟的完成用 'F'（finish）
    enqueue("F", job_id ? job_id : "");
}

void behavior_runtime_enqueue_cancel(const char* job_id)
{
    enqueue("C", job_id ? job_id : "");
}

void behavior_runtime_on_voice_activity()
{
    s_voice_activity = true;
    // 语音优先：遥控台立即释放视频媒体，给 I2S 让出 DMA 内存
    teleop_on_voice_activity();
}

void behavior_runtime_update()
{
    // 遥控台：主循环里应用目标/超时/扫描（运动 API 不能在 WS 回调线程里调）
    teleop_update();

    if (s_queue == nullptr) {
        return;
    }

    char* message = nullptr;
    while (xQueueReceive(s_queue, &message, 0) == pdTRUE) {
        if (message == nullptr) {
            continue;
        }

        if (message[0] == 'C') {
            if (s_active) {
                cancel_job("cancelled");
            }
            s_has_pending = false;
            s_pending = Job();
            ESP_LOGI(TAG, "cancel requested");
        } else if (message[0] == 'T') {
            // 番茄钟是后台状态，不占执行器：直接交给 pomodoro 维护
            pomodoro_apply(message + 1);
        } else if (message[0] == 'X') {
            pomodoro_cancel(message + 1);
        } else if (message[0] == 'P') {
            pomodoro_panel_open();
        } else if (message[0] == 'F') {
            // App 上点了「完成」：演一遍完成版（一次点头），不进取
            pomodoro_complete(message + 1);
        } else if (message[0] == 'B') {
            Job job;
            if (parse_script(std::string(message + 1), job)) {
                if (s_active) {
                    cancel_job("preempted");
                }
                s_pending = std::move(job);
                s_has_pending = true;
            } else {
                ESP_LOGW(TAG, "invalid behavior script");
            }
        }
        free(message);
    }

    pomodoro_update();

    // 用户唤醒/点屏幕：行为立刻停，语音优先
    if (s_voice_activity.exchange(false)) {
        if (s_active) {
            Application::GetInstance().GetAudioService().ResetDecoder();
            cancel_job("voice_preempted");
        }
        return;
    }

    if (Application::GetInstance().GetDeviceState() == kDeviceStateSpeaking) {
        if (s_active) {
            cancel_job("voice_preempted");
        }
        return;
    }

    if (!s_active && s_has_pending) {
        start_job(std::move(s_pending));
        s_pending = Job();
        s_has_pending = false;
    }

    if (s_active) {
        update_step();
    }
}
