#pragma once

/**
 * @brief 把当前屏幕以 SNAPBIN 协议打到串口，供 tools/snap.py 解析。
 *
 * 协议：`SNAPBIN <w> <h>\n` + w*h*2 字节 RGB565(LE) + `\nSNAPEND\n`
 * 分辨率固定 240x180（320x240 太大，串口 115200 要传 13 秒）。
 */
void chan_snapshot_request();  // 线程安全，任意任务可调
void chan_snapshot_poll();     // 主循环调用（必须已持 LVGL 锁）
