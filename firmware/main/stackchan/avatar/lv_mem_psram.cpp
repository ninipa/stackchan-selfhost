/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include <lvgl.h>
#include <esp_heap_caps.h>
#include <cstring>

/*
 * LVGL 的内存分配器（CONFIG_LV_USE_CUSTOM_MALLOC=y）。
 *
 * 1.6.2 的官方配置是 CLIB malloc + SPIRAM_MALLOC_ALWAYSINTERNAL=512：
 * 小于 512 字节的分配全部落到内部 RAM。LVGL 的 UI 对象/样式/事件描述符
 * 大多是 50~300 字节，数量一多就把内部 RAM 切碎——AFE 初始化后最大连续块
 * 只剩 2.5KB，MQTT 的 UDP 音频任务（3KB 栈）因此创建失败。
 *
 * 这里让 LVGL 优先使用 PSRAM，只在 PSRAM 失败时回退内部 RAM。
 * 其它子系统（Wi-Fi / MQTT / 驱动）的 malloc 行为不变。
 */
extern "C" void lv_mem_init(void)
{
}

extern "C" void lv_mem_deinit(void)
{
}

extern "C" void* lv_malloc_core(size_t size)
{
    void* p = heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (p == nullptr) {
        p = heap_caps_malloc(size, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    }
    return p;
}

extern "C" void lv_free_core(void* p)
{
    heap_caps_free(p);
}

extern "C" void* lv_realloc_core(void* p, size_t new_size)
{
    void* n = heap_caps_realloc(p, new_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (n == nullptr) {
        n = heap_caps_realloc(p, new_size, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    }
    return n;
}

extern "C" void lv_mem_monitor_core(lv_mem_monitor_t* mon_p)
{
    if (mon_p != nullptr) {
        std::memset(mon_p, 0, sizeof(*mon_p));
    }
}

extern "C" lv_result_t lv_mem_test_core(void)
{
    return LV_RESULT_OK;
}
