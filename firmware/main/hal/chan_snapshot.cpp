#include "chan_snapshot.h"

#include <algorithm>
#include <cstdio>
#include <cstring>

#include <esp_heap_caps.h>
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <lvgl.h>

#include "behavior/behavior_client.h"
#include "hal.h"

#define TAG "Snapshot"

namespace {

TaskHandle_t s_task = nullptr;
// 只能小分块：16KB 一帧会让 WS 直接断（errno 104）。2KB 慢（约 0.7s/块）但稳定，
// 240x180 = 86KB ≈ 40 块 ≈ 30 秒。够用，别贪大。
constexpr size_t kChunk = 2048;
constexpr int kOutW = 240;           // 降一档分辨率：320x240 帧太大，传输也更慢
constexpr int kOutH = 180;

/**
 * 截图任务：抓帧 → 转成紧排 RGB565 → 通过控制 WS 分块上传。
 *
 * 刻意不碰串口：console 和日志共用同一条链路，之前在主循环里连写 7.5 秒
 * 把任务卡死、看门狗进 panic，整个设备僵住（2026-11-08 实测）。
 * LVGL 锁只在 lv_snapshot_take 那一瞬间持有，剩下的搬运/发送都在锁外。
 */
void snapshot_task(void*)
{
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

        lv_draw_buf_t* buf = nullptr;
        {
            LvglLockGuard lock;
            buf = lv_snapshot_take(lv_screen_active(), LV_COLOR_FORMAT_RGB565);
        }
        if (buf == nullptr) {
            ESP_LOGW(TAG, "snapshot failed");
            behavior_client_send_json("{\"type\":\"snapshot_end\"}");
            continue;
        }

        const int sw = buf->header.w;
        const int sh = buf->header.h;
        const uint32_t stride = buf->header.stride;
        const int w = kOutW;
        const int h = kOutH;
        const size_t need = static_cast<size_t>(w) * h * 2;

        auto* out = static_cast<uint8_t*>(heap_caps_malloc(need, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
        if (out == nullptr) {
            lv_draw_buf_destroy(buf);
            ESP_LOGW(TAG, "no buffer for %u bytes", static_cast<unsigned>(need));
            behavior_client_send_json("{\"type\":\"snapshot_end\"}");
            continue;
        }

        // 最近邻降采样；LVGL 的 stride 可能带对齐填充，所以逐像素取
        for (int y = 0; y < h; y++) {
            const int sy = y * sh / h;
            const uint8_t* row = buf->data + static_cast<size_t>(sy) * stride;
            uint8_t* dst = out + static_cast<size_t>(y) * w * 2;
            for (int x = 0; x < w; x++) {
                const int sx = x * sw / w;
                dst[x * 2]     = row[sx * 2];
                dst[x * 2 + 1] = row[sx * 2 + 1];
            }
        }
        lv_draw_buf_destroy(buf);

        char header[96];
        snprintf(header, sizeof(header), "{\"type\":\"snapshot_begin\",\"w\":%d,\"h\":%d,\"len\":%u}", w, h,
                 static_cast<unsigned>(need));
        const uint32_t started = GetHAL().millis();
        behavior_client_send_json(header);

        bool ok = true;
        for (size_t off = 0; off < need; off += kChunk) {
            const size_t n = std::min(kChunk, need - off);
            if (!behavior_client_send_binary(out + off, n)) {
                ok = false;
                break;
            }
            vTaskDelay(1);  // 别把 Wi-Fi 任务饿着
        }
        behavior_client_send_json("{\"type\":\"snapshot_end\"}");
        heap_caps_free(out);

        ESP_LOGI(TAG, "snapshot %dx%d %s in %ums", w, h, ok ? "sent" : "aborted",
                 (unsigned)(GetHAL().millis() - started));
    }
}

}  // namespace

void chan_snapshot_request()
{
    if (s_task == nullptr) {
        xTaskCreate(snapshot_task, "chan_snap", 8192, nullptr, 3, &s_task);
    }
    if (s_task != nullptr) {
        xTaskNotifyGive(s_task);
    }
}

void chan_snapshot_poll()
{
    // 主循环不再参与：保留空实现，避免改动调用点
}
