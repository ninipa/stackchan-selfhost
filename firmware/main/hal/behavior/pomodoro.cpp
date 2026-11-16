#include "pomodoro.h"
#include "pose.h"

#include <board.h>
#include <cJSON.h>
#include <display.h>
#include <esp_log.h>
#include "../board/hal_bridge.h"
#include "behavior_client.h"
#include <hal/hal.h>
#include <stackchan/avatar/skins/default/countdown_overlay.h>
#include <stackchan/stackchan.h>

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>

#define TAG "Pomodoro"

namespace {

using stackchan::avatar::DefaultCountdownOverlay;

std::unique_ptr<DefaultCountdownOverlay> s_overlay;

struct State {
    bool active = false;
    std::string job_id;
    int duration_ms = 0;
    int remain_at_apply = 0;  // 锚点时的剩余
    int overtime_at_apply = 0;
    uint32_t anchor = 0;
    std::string label = "专注";
    std::string task;

    int phase = -1;  // -1 未知 0 专注 1 引线 2 超时
    uint32_t last_paint = 0;
    bool yielded = false;  // 因语音让位
    uint32_t suppress_until = 0;  // 气泡占用屏幕的截止时刻

    bool celebrating = false;
    int cele_step = 0;
    uint32_t cele_next = 0;
    bool celebrated = false;
    int finish_kind = 0;  // 0 = 到点（会进超时），1 = 提前完成（直接收工）

    // 长按抽屉
    bool panel_open = false;
    uint32_t panel_deadline = 0;
    uint32_t panel_paint = 0;

    // 最近一次在本机结束的 job：断线时点了完成，服务端还以为在跑，
    // 重连会重新下发这一段；用它挡掉，避免 HUD 又冒出来。
    std::string done_job;
    uint32_t done_at = 0;
};

State s;

// 表演用的俯仰姿态统一取自 pose.h（0.1° 单位），不再用归一化坐标：
// 归一化的 0 实际是 45°，而"常态位"是 20°，两者混用会让表演完的头比平时高一截
// （2026-11-08 反馈"没回正"、2026-11-10 定案统一到 pose.h）。


Display* display()
{
    return Board::GetInstance().GetDisplay();
}

const char* jstr(const cJSON* obj, const char* key, const char* fallback)
{
    const cJSON* item = cJSON_GetObjectItem(obj, key);
    return cJSON_IsString(item) ? item->valuestring : fallback;
}

int jint(const cJSON* obj, const char* key, int fallback)
{
    const cJSON* item = cJSON_GetObjectItem(obj, key);
    return cJSON_IsNumber(item) ? item->valueint : fallback;
}

void ensure_overlay()
{
    if (s_overlay) {
        return;
    }
    s_overlay = std::make_unique<DefaultCountdownOverlay>(lv_screen_active());
    ESP_LOGI(TAG, "overlay created");
}

int remain_ms(uint32_t now)
{
    return s.remain_at_apply - static_cast<int>(now - s.anchor);
}

void set_led(uint8_t r, uint8_t g, uint8_t b)
{
    GetHAL().setRgbColorPair(r, g, b);
    GetHAL().refreshRgb();
}

void bubble(const char* text)
{
    if (display() != nullptr) {
        display()->SetChatMessage("assistant", text);
    }
}

void reset_celebration()
{
    s.celebrating = false;
    s.cele_step = 0;
    s.cele_next = 0;
    s.finish_kind = 0;
}

void finish_celebration(bool finished, bool interrupted);

/** 记下"这一段已经在本机结束了"：重连对表时用它挡掉服务端补发的同一条。 */
void remember_done()
{
    s.done_job = s.job_id;
    s.done_at = GetHAL().millis();
}

/** 到点 / 提前完成的表演。
 *
 *  到点：happy → 点头两次 → 绿闪 → 气泡「任务名，时间到」→ 交回超时红字（sleepy）
 *  完成：happy → 点头一次 → 绿闪 → 气泡「任务名，完成」→ 收 HUD（neutral）
 *
 *  都刻意很短（< 3s）：到点时人通常在忙，长动画是打扰。
 */
void step_celebration(uint32_t now)
{
    if (now < s.cele_next) {
        return;
    }

    auto& motion = GetStackChan().motion();
    const uint32_t beat = 480;
    const bool finished = s.finish_kind == 1;
    const int nod_steps = finished ? 2 : 4;  // 一次点头 = 下 + 上

    if (s.cele_step == 0) {
        ESP_LOGI(TAG, "celebration start (finished=%d)", finished ? 1 : 0);
        if (display() != nullptr) {
            display()->SetEmotion("happy");
        }
        char text[96];
        if (s.task.empty()) {
            snprintf(text, sizeof(text), "%s", finished ? "完成" : "时间到");
        } else {
            snprintf(text, sizeof(text), finished ? "%s，完成" : "%s，时间到", s.task.c_str());
        }
        bubble(text);
        set_led(0, 170, 90);

        if (!finished) {
            // 到点：让服务端补一句人声（"<任务名>，时间到"），合成 1~2 秒正好接在点头之后
            char done_msg[96];
            snprintf(done_msg, sizeof(done_msg), "{\"type\":\"timer_done\",\"job_id\":\"%s\"}",
                     s.job_id.c_str());
            behavior_client_send_json(done_msg);
        }
        // 表演期间锁住动作，否则 idle_motion 会在几秒内把目标角度抢回去
        motion.setModifyLock(true);
        motion.moveWithSpeed(0, chan_pose::kPitchHome, 600);
        s.cele_next = now + beat;
        s.cele_step = 1;
        return;
    }

    if (s.cele_step <= nod_steps) {
        // 点头用归一化坐标：moveWithSpeed 的角度是舵机单位（0.1 度），
        // 早先写的 12 只等于 1.2°，肉眼完全看不出来（2026-11-08 用户实测反馈）
        const bool down = (s.cele_step % 2) == 1;
        motion.moveWithSpeed(0, down ? chan_pose::kPitchNod : chan_pose::kPitchHome, 600);
        s.cele_next = now + beat;
        s.cele_step++;
        return;
    }

    if (s.cele_step == nod_steps + 1) {
        motion.moveWithSpeed(0, chan_pose::kPitchHome, 500);
        motion.setModifyLock(false);  // 把动作交回给 idle
        set_led(0, 0, 0);
        s.cele_next = now + 1800;  // 留出时间让气泡散去
        s.cele_step++;
        return;
    }

    finish_celebration(finished, false);
}

/** 表演收尾：正常演完和被语音打断都走这里，避免停在 happy 上。
 *
 *  interrupted 只用于日志——收尾后的脸永远按结局给（完成→中性，到点→困脸）。
 */
void finish_celebration(bool finished, bool interrupted)
{
    reset_celebration();
    bubble("");
    set_led(0, 0, 0);
    GetStackChan().motion().setModifyLock(false);
    GetStackChan().motion().moveWithSpeed(0, chan_pose::kPitchHome, 500);

    if (finished) {
        remember_done();
        s.active = false;
        s.job_id.clear();
        s.phase = -1;
        s.celebrated = false;
        if (display() != nullptr) {
            display()->SetEmotion("neutral");
        }
        if (s_overlay) {
            s_overlay->hide();
        }
        ESP_LOGI(TAG, "timer finished (interrupted=%d)", interrupted ? 1 : 0);
        return;
    }

    if (display() != nullptr) {
        // 困脸是超时态的信号，被打断也要给。到点那句「时间到」的播报正好配着它。
        display()->SetEmotion("sleepy");
    }
    s.phase = -1;
    s.last_paint = 0;
    ESP_LOGI(TAG, "timer hit zero, overtime face restored (interrupted=%d)", interrupted ? 1 : 0);
}

void paint(uint32_t now, int remain)
{
    const int total = s.duration_ms > 0 ? s.duration_ms : 1;
    const int fuse_at = total * 2 / 10;  // 剩 20% 切全屏引线
    int phase;
    if (remain > fuse_at) {
        phase = 0;
    } else if (remain > 0) {
        phase = 1;
    } else {
        phase = 2;
    }

    const int prev_phase = s.phase;
    if (phase != s.phase) {
        s.phase = phase;
        s.last_paint = 0;
    }

    // 进入引线阶段（0 → 1）上报一次，服务端据此播「要抓紧了哟」。
    // 只在 0→1 时发：重连补发时 phase 从 -1 直接变 1，不会重复播。
    // 剩余不足 4 秒就不发——话没说完就到点，反而更乱。
    if (prev_phase == 0 && phase == 1 && remain > 4000 && s.active && !s.job_id.empty()) {
        char fuse_msg[96];
        snprintf(fuse_msg, sizeof(fuse_msg), "{\"type\":\"timer_fuse\",\"job_id\":\"%s\"}", s.job_id.c_str());
        behavior_client_send_json(fuse_msg);
        ESP_LOGI(TAG, "fuse phase entered, remain %dms", remain);
    }

    // 专注/超时 2Hz，引线 4Hz（火焰和火星靠这个节拍抖）
    const uint32_t interval = phase == 1 ? 250 : 500;
    if (s.last_paint != 0 && now - s.last_paint < interval) {
        return;
    }
    s.last_paint = now;

    ensure_overlay();

    if (phase == 0) {
        const int done = total > 0 ? std::clamp((total - remain) * 100 / total, 0, 100) : 0;
        s_overlay->showBand(remain, s.label.c_str(), s.task.c_str(), done);
    } else if (phase == 1) {
        // 副标题放任务名：大字已经在倒计时，再写"最后 N 分钟"是三重强调，
        // 而且 20% 窗口不足一分钟时那句话是错的（12 秒窗口写"最后 1 分钟"）。
        s_overlay->showFuse(remain, fuse_at, s.task.c_str());
    } else {
        s_overlay->showOvertime(-remain, "超时");
    }
}

}  // namespace

void pomodoro_apply(const char* json)
{
    cJSON* root = cJSON_Parse(json);
    if (root == nullptr) {
        ESP_LOGW(TAG, "invalid timer json");
        return;
    }

    const char* job = jstr(root, "job_id", "");
    if (job[0] == '\0') {
        cJSON_Delete(root);
        return;
    }

    // 断线时在本机点了完成/放弃，服务端还不知道；重连后会把同一段又推回来。
    // 两分钟内看到同一个 job 一律忽略，避免 HUD 已经收了又冒出来。
    if (s.done_job == job && (GetHAL().millis() - s.done_at) < 120000) {
        ESP_LOGI(TAG, "ignore timer %s: closed on device %ums ago", job,
                 (unsigned)(GetHAL().millis() - s.done_at));
        cJSON_Delete(root);
        return;
    }

    const int total = jint(root, "duration_ms", 0);
    const int remain = jint(root, "remaining_ms", total);
    const int overtime = jint(root, "overtime_ms", 0);
    const char* label = jstr(root, "label", "专注");
    const char* task = jstr(root, "task", "");

    const bool same = s.active && s.job_id == job;
    if (!same) {
        reset_celebration();
        s.celebrated = false;
        s.phase = -1;
        s.last_paint = 0;
        s.yielded = false;
        s.job_id = job;
        // 新一段开始：脸回中性。但如果这段**已经超时**（重启/重连时服务端补发的），
        // 直接给困脸——否则重启一次，屏幕上的困态就丢了。
        if (display() != nullptr) {
            display()->SetEmotion(remain <= 0 ? "sleepy" : "neutral");
        }
        GetStackChan().motion().moveWithSpeed(0, chan_pose::kPitchHome, 500);
        ESP_LOGI(TAG, "timer %s: %dms, remaining %dms", job, total, remain);
    } else {
        ESP_LOGI(TAG, "timer %s resync: remaining %dms", job, remain);
    }

    s.active = true;
    s.duration_ms = total;
    s.remain_at_apply = remain;
    s.overtime_at_apply = overtime;
    s.anchor = GetHAL().millis();
    s.label = label;
    s.task = task;

    // 掉线跨过 0 点：不补放整段表演，只在超时不久时补一次
    if (remain <= 0 && overtime > 60000) {
        s.celebrated = true;
    }

    cJSON_Delete(root);
}

void pomodoro_cancel(const char* job_id)
{
    if (!s.active) {
        return;
    }
    if (job_id != nullptr && job_id[0] != '\0' && s.job_id != job_id) {
        return;
    }

    ESP_LOGI(TAG, "timer %s canceled", s.job_id.c_str());
    remember_done();  // 断线时被取消也要挡住重连后的补发
    s.active = false;
    s.job_id.clear();
    reset_celebration();
    s.celebrated = false;
    s.phase = -1;
    s.last_paint = 0;
    s.yielded = false;
    s.panel_open = false;
    GetStackChan().motion().moveWithSpeed(0, chan_pose::kPitchHome, 500);  // 回正
    GetStackChan().motion().setModifyLock(false);  // 表演中途被打断也要交回动作
    set_led(0, 0, 0);
    // 取消要无条件收：表演刚结束就取消时，气泡和"困"脸还挂在屏幕上
    bubble("");
    if (display() != nullptr) {
        display()->SetEmotion("neutral");
    }
    if (s_overlay) {
        s_overlay->hide();
    }
}

void pomodoro_yield_ms(uint32_t ms)
{
    s.suppress_until = GetHAL().millis() + ms;
}

bool pomodoro_running()
{
    return s.active;
}

bool pomodoro_overtime()
{
    if (!s.active) {
        return false;
    }
    // 过了 0 点才算超时（超时 10 分钟后 s.active 会被清掉，这里自然变 false）
    return remain_ms(GetHAL().millis()) <= 0;
}

bool pomodoro_hud_visible()
{
    // 说话/表演时 HUD 不在屏幕上，这时候长按应该还是"中止回复"，不该弹面板
    return s.active && !s.yielded && !s.celebrating;
}

bool pomodoro_panel_active()
{
    return s.panel_open;
}

void pomodoro_panel_dismiss()
{
    if (!s.panel_open) {
        return;
    }
    s.panel_open = false;
    if (s_overlay) {
        s_overlay->hidePanel();
    }
    s.last_paint = 0;  // 让下面的信息带立刻重画
    ESP_LOGI(TAG, "panel closed");
}

void pomodoro_panel_open()
{
    if (!pomodoro_hud_visible() || s.panel_open) {
        return;
    }
    ensure_overlay();
    const uint32_t now = GetHAL().millis();
    const int remain = remain_ms(now);
    s.panel_open = true;
    s.panel_deadline = now + 10000;  // 10 秒没人动就自己关，避免一直占屏
    s_overlay->showPanel(s.label.c_str(), remain);
    ESP_LOGI(TAG, "panel opened (remain %dms)", remain);
}

/** 提前收工：本地立刻表演，同时把意图发给服务端（结局由服务端按时间判） */
static void finish_on_device()
{
    if (!s.active || s.celebrating) {
        return;
    }
    char msg[96];
    snprintf(msg, sizeof(msg), "{\"type\":\"timer_finished\",\"job_id\":\"%s\"}", s.job_id.c_str());
    behavior_client_send_json(msg);

    s.finish_kind = 1;
    s.celebrating = true;
    s.cele_step = 0;
    s.cele_next = GetHAL().millis();
    if (s_overlay) {
        s_overlay->hide();  // 表演期间把屏幕还给脸和气泡
    }
    ESP_LOGI(TAG, "finished from panel: %s", s.job_id.c_str());
}

/** 放弃：不庆祝，直接收工 */
static void abandon_on_device()
{
    if (!s.active) {
        return;
    }
    char msg[96];
    snprintf(msg, sizeof(msg), "{\"type\":\"timer_abandon\",\"job_id\":\"%s\"}", s.job_id.c_str());
    behavior_client_send_json(msg);

    remember_done();
    s.active = false;
    s.job_id.clear();
    s.phase = -1;
    s.celebrated = false;
    set_led(0, 0, 0);
    if (display() != nullptr) {
        display()->SetEmotion("neutral");
    }
    GetStackChan().motion().moveWithSpeed(0, chan_pose::kPitchHome, 500);
    GetStackChan().motion().setModifyLock(false);
    if (s_overlay) {
        s_overlay->hide();
    }
    ESP_LOGI(TAG, "abandoned from panel");
}

void pomodoro_panel_tap(int x, int y)
{
    if (!s.panel_open || !s_overlay) {
        return;
    }
    switch (s_overlay->panelHitTest(x, y)) {
        case DefaultCountdownOverlay::PanelHit::Complete:
            pomodoro_panel_dismiss();
            finish_on_device();
            break;
        case DefaultCountdownOverlay::PanelHit::Abandon:
            pomodoro_panel_dismiss();
            abandon_on_device();
            break;
        case DefaultCountdownOverlay::PanelHit::Continue:
            pomodoro_panel_dismiss();
            break;
        case DefaultCountdownOverlay::PanelHit::None:
        default:
            break;  // 抽屉里的空白：什么都不做
    }
}

void pomodoro_complete(const char* job_id)
{
    if (!s.active || s.celebrating) {
        return;
    }
    if (job_id != nullptr && job_id[0] != '\0' && s.job_id != job_id) {
        return;
    }
    ESP_LOGI(TAG, "complete pushed from app: %s", s.job_id.c_str());
    s.panel_open = false;
    s.finish_kind = 1;
    s.celebrating = true;
    s.cele_step = 0;
    s.cele_next = GetHAL().millis();
    if (s_overlay) {
        s_overlay->hide();
    }
}

void pomodoro_update()
{
    if (!s.active) {
        return;
    }

    const uint32_t now = GetHAL().millis();

    // 语音优先：说话/聆听/播报时把屏幕让给气泡和表情，计时照走
    const bool suppressed = s.suppress_until != 0 && static_cast<int32_t>(s.suppress_until - now) > 0;
    const bool voice_busy = !hal_bridge::is_xiaozhi_idle() || suppressed;
    if (voice_busy) {
        // 只有"真正的语音状态"才把表演收尾。单纯的让位窗口（suppressed，例如到点那句
        // 「时间到」的播报）不算——否则人声一响就把表演截断，后面也出不来困脸。
        if (s.celebrating && !hal_bridge::is_xiaozhi_idle()) {
            ESP_LOGW(TAG, "celebration interrupted by voice, finishing now");
            const bool finished = s.finish_kind == 1;
            finish_celebration(finished, true);
        }
        if (!s.yielded) {
            ensure_overlay();
            s_overlay->hide();  // hide() 里会一并收掉抽屉
            s.yielded = true;
            s.panel_open = false;
            s.phase = -1;
        }
        return;
    }
    if (s.yielded) {
        s.yielded = false;
        s.last_paint = 0;
    }

    const int remain = remain_ms(now);

    // 抽屉：10 秒无操作自动关；**即将**到点时先收掉，免得盖住表演。
    // 注意不能写成 remain <= 0：超时态 remain 一直是负的，那样抽屉一打开就秒关，
    // 用户就再也点不到「完成 / 放弃任务」了（2026-11-08 实测踩到）。
    if (s.panel_open) {
        const bool about_to_finish = (remain <= 0 && !s.celebrated);
        if (static_cast<int32_t>(s.panel_deadline - now) <= 0 || about_to_finish) {
            pomodoro_panel_dismiss();
        } else if (now - s.panel_paint >= 500) {
            s.panel_paint = now;
            s_overlay->showPanel(s.label.c_str(), remain);
        }
    }

    // 超时太久就别一直占着屏幕了（设计稿：10 分钟后停止提醒）
    if (remain <= -600000) {
        ESP_LOGI(TAG, "overtime over 10min, hiding HUD");
        s.active = false;
        s.job_id.clear();
        s.celebrated = false;
        s.panel_open = false;
        s.phase = -1;
        if (s_overlay) {
            s_overlay->hide();
        }
        if (display() != nullptr) {
            display()->SetEmotion("neutral");
        }
        return;
    }

    if (s.celebrating) {
        step_celebration(now);
        return;
    }

    if (remain <= 0 && !s.celebrated) {
        s.celebrated = true;
        // 迟到太久（比如一直在说话）就别再庆祝了，直接进超时信息带
        if (-remain <= 60000) {
            s.celebrating = true;
            s.cele_step = 0;
            s.cele_next = now;
            ensure_overlay();
            s_overlay->hide();  // 表演期间把屏幕还给脸和气泡
            return;
        }
        s.phase = -1;
        s.last_paint = 0;
    }

    paint(now, remain);
}
