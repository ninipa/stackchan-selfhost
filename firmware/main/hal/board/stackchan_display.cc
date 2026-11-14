/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "stackchan_display.h"
#include <esp_log.h>
#include <esp_err.h>
#include <esp_lvgl_port.h>
#include <esp_psram.h>
#include <vector>
#include <cstring>
#include <src/misc/cache/lv_cache.h>
#include <settings.h>
#include <lvgl.h>
#include <lvgl_theme.h>
#include <stackchan/stackchan.h>
#include <stackchan/avatar/decorators/decorators.h>
#include <assets/lang_config.h>
#include <hal/hal.h>
#include "hal/behavior/behavior_runtime.h"
#include "hal/behavior/pose.h"

using namespace stackchan;
using namespace stackchan::avatar;

#define TAG "StackChanAvatarDisplay"

LV_IMAGE_DECLARE(decorator_question);
LV_IMAGE_DECLARE(decorator_exclam);

LV_FONT_DECLARE(BUILTIN_TEXT_FONT);
LV_FONT_DECLARE(BUILTIN_ICON_FONT);
LV_FONT_DECLARE(font_material_symbols_30_4);

// Have to register themes, so the asset apply can update the text font
void StackChanAvatarDisplay::InitializeLcdThemes()
{
    auto text_font       = std::make_shared<LvglBuiltInFont>(&BUILTIN_TEXT_FONT);
    auto icon_font       = std::make_shared<LvglBuiltInFont>(&BUILTIN_ICON_FONT);
    auto large_icon_font = std::make_shared<LvglBuiltInFont>(&font_material_symbols_30_4);

    // light theme
    auto light_theme = new LvglTheme("light");
    light_theme->set_background_color(lv_color_hex(0xFFFFFF));        // rgb(255, 255, 255)
    light_theme->set_text_color(lv_color_hex(0x000000));              // rgb(0, 0, 0)
    light_theme->set_chat_background_color(lv_color_hex(0xE0E0E0));   // rgb(224, 224, 224)
    light_theme->set_user_bubble_color(lv_color_hex(0x00FF00));       // rgb(0, 128, 0)
    light_theme->set_assistant_bubble_color(lv_color_hex(0xDDDDDD));  // rgb(221, 221, 221)
    light_theme->set_system_bubble_color(lv_color_hex(0xFFFFFF));     // rgb(255, 255, 255)
    light_theme->set_system_text_color(lv_color_hex(0x000000));       // rgb(0, 0, 0)
    light_theme->set_border_color(lv_color_hex(0x000000));            // rgb(0, 0, 0)
    light_theme->set_low_battery_color(lv_color_hex(0x000000));       // rgb(0, 0, 0)
    light_theme->set_text_font(text_font);
    light_theme->set_icon_font(icon_font);
    light_theme->set_large_icon_font(large_icon_font);

    // dark theme
    auto dark_theme = new LvglTheme("dark");
    dark_theme->set_background_color(lv_color_hex(0x000000));        // rgb(0, 0, 0)
    dark_theme->set_text_color(lv_color_hex(0xFFFFFF));              // rgb(255, 255, 255)
    dark_theme->set_chat_background_color(lv_color_hex(0x1F1F1F));   // rgb(31, 31, 31)
    dark_theme->set_user_bubble_color(lv_color_hex(0x00FF00));       // rgb(0, 128, 0)
    dark_theme->set_assistant_bubble_color(lv_color_hex(0x222222));  // rgb(34, 34, 34)
    dark_theme->set_system_bubble_color(lv_color_hex(0x000000));     // rgb(0, 0, 0)
    dark_theme->set_system_text_color(lv_color_hex(0xFFFFFF));       // rgb(255, 255, 255)
    dark_theme->set_border_color(lv_color_hex(0xFFFFFF));            // rgb(255, 255, 255)
    dark_theme->set_low_battery_color(lv_color_hex(0xFF0000));       // rgb(255, 0, 0)
    dark_theme->set_text_font(text_font);
    dark_theme->set_icon_font(icon_font);
    dark_theme->set_large_icon_font(large_icon_font);

    auto& theme_manager = LvglThemeManager::GetInstance();
    theme_manager.RegisterTheme("light", light_theme);
    theme_manager.RegisterTheme("dark", dark_theme);
}

StackChanAvatarDisplay::StackChanAvatarDisplay(esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_handle_t panel,
                                               int width, int height, int offset_x, int offset_y, bool mirror_x,
                                               bool mirror_y, bool swap_xy)
    : LvglDisplay(), panel_io_(panel_io), panel_(panel)
{
    width_  = width;
    height_ = height;

    // Initialize LCD themes
    InitializeLcdThemes();

    // Load theme from settings
    Settings settings("display", false);
    std::string theme_name = settings.GetString("theme", "light");
    current_theme_         = LvglThemeManager::GetInstance().GetTheme(theme_name);

    // Draw white screen
    std::vector<uint16_t> buffer(width_, 0xFFFF);
    for (int y = 0; y < height_; y++) {
        esp_lcd_panel_draw_bitmap(panel_, 0, y, width_, y + 1, buffer.data());
    }

    // Set the display to on
    ESP_LOGI(TAG, "Turning display on");
    {
        esp_err_t __err = esp_lcd_panel_disp_on_off(panel_, true);
        if (__err == ESP_ERR_NOT_SUPPORTED) {
            ESP_LOGW(TAG, "Panel does not support disp_on_off; assuming ON");
        } else {
            ESP_ERROR_CHECK(__err);
        }
    }

    ESP_LOGI(TAG, "Initialize LVGL library");
    lv_init();

#if CONFIG_SPIRAM
    // lv image cache, currently only PNG is supported
    size_t psram_size_mb = esp_psram_get_size() / 1024 / 1024;
    if (psram_size_mb >= 8) {
        lv_image_cache_resize(2 * 1024 * 1024, true);
        ESP_LOGI(TAG, "Use 2MB of PSRAM for image cache");
    } else if (psram_size_mb >= 2) {
        lv_image_cache_resize(512 * 1024, true);
        ESP_LOGI(TAG, "Use 512KB of PSRAM for image cache");
    }
#endif

    ESP_LOGI(TAG, "Initialize LVGL port");
    lvgl_port_cfg_t port_cfg = ESP_LVGL_PORT_INIT_CONFIG();
    // port_cfg.task_priority   = 20;
    port_cfg.task_priority = 3;
#if CONFIG_SOC_CPU_CORES_NUM > 1
    port_cfg.task_affinity = 1;
#endif
    lvgl_port_init(&port_cfg);

    ESP_LOGI(TAG, "Adding LCD display");
    const lvgl_port_display_cfg_t display_cfg = {
        .io_handle      = panel_io_,
        .panel_handle   = panel_,
        .control_handle = nullptr,
        // 20 行 = 12,800 字节，全部占内部 DMA RAM。播放语音时 DMA 最大连续块
        // 会掉到 416 字节，Wi-Fi TX buffer（1,600 字节）分配失败 → TCP ACK 发不出去
        // → 声音断断续续。降到 10 行（6,400 字节）让出 6.4KB。
        .buffer_size    = static_cast<uint32_t>(width_ * 10),
        .double_buffer  = false,
        .trans_size     = 0,
        .hres           = static_cast<uint32_t>(width_),
        .vres           = static_cast<uint32_t>(height_),
        .monochrome     = false,
        .rotation =
            {
                .swap_xy  = swap_xy,
                .mirror_x = mirror_x,
                .mirror_y = mirror_y,
            },
        .color_format = LV_COLOR_FORMAT_RGB565,
        .flags =
            {
                .buff_dma     = 1,
                .buff_spiram  = 0,
                .sw_rotate    = 0,
                .swap_bytes   = 1,
                .full_refresh = 0,
                .direct_mode  = 0,
            },
    };

    display_ = lvgl_port_add_disp(&display_cfg);
    if (display_ == nullptr) {
        ESP_LOGE(TAG, "Failed to add display");
        return;
    }

    if (offset_x != 0 || offset_y != 0) {
        lv_display_set_offset(display_, offset_x, offset_y);
    }

    // Create a timer to hide the preview image
    esp_timer_create_args_t preview_timer_args = {
        .callback =
            [](void* arg) {
                StackChanAvatarDisplay* display = static_cast<StackChanAvatarDisplay*>(arg);
                display->SetPreviewImage(nullptr);
            },
        .arg                   = this,
        .dispatch_method       = ESP_TIMER_TASK,
        .name                  = "preview_timer",
        .skip_unhandled_events = false,
    };
    esp_timer_create(&preview_timer_args, &preview_timer_);

    // Create boot logo label if not warm boot
    if (GetHAL().getWarmRebootTarget() < 0) {
        ESP_LOGI(TAG, "Create boot logo label");
        Lock();
        {
            uitk::lvgl_cpp::ScreenActive screen;
            screen.setBgColor(lv_color_hex(0x000000));
        }
        GetHAL().bootLogo = std::make_unique<BootLogo>();
        Unlock();
    }

    // Robot will be created later in SetupXiaoZhiUI()
}

StackChanAvatarDisplay::~StackChanAvatarDisplay()
{
    ESP_LOGI(TAG, "Destroying StackChanAvatarDisplay");

    if (preview_timer_ != nullptr) {
        esp_timer_stop(preview_timer_);
        esp_timer_delete(preview_timer_);
    }

    if (preview_image_ != nullptr) {
        lv_obj_del(preview_image_);
    }

    auto& stackchan = GetStackChan();
    if (stackchan.hasAvatar()) {
        stackchan.resetAvatar();
    }
}

bool StackChanAvatarDisplay::Lock(int timeout_ms)
{
    return lvgl_port_lock(timeout_ms);
}

void StackChanAvatarDisplay::Unlock()
{
    lvgl_port_unlock();
}

lv_disp_t* StackChanAvatarDisplay::GetLvglDisplay()
{
    return display_;
}

#include <hal/board/hal_bridge.h>

void StackChanAvatarDisplay::SetupUI()
{
    // Prevent duplicate calls - if already called, return early
    if (setup_ui_called_) {
        ESP_LOGW(TAG, "SetupUI() called multiple times, skipping duplicate call");
        return;
    }

    Display::SetupUI();  // Mark SetupUI as called

    auto& stackchan = GetStackChan();

    if (stackchan.hasAvatar()) {
        ESP_LOGW(TAG, "Avatar already created");
        return;
    }

    DisplayLockGuard lock(this);

    ESP_LOGI(TAG, "Creating Stack-chan Avatar...");

    auto avatar = std::make_unique<DefaultAvatar>();
    avatar->init(lv_screen_active());

    // 2026-11-08：点击/长按改由 Hal::lvgl_read_cb 在 indev 层判定。
    // 这里原先的 panel->onClick() + 2s 防抖，实测会吞掉"进入 listen 后立刻再点退出"；
    // 而 LVGL 的 CLICKED 本身也不可靠（同坐标两次点按，有一次不出事件）。
    // 详见 hal.cpp 里 kTouchLongPressMs 的注释。

    stackchan.attachAvatar(std::move(avatar));

    // 新头像没有任何装饰器，旧的 id 已经失效
    emotion_decorator_ids_.clear();
    emotion_decorator_key_ = -1;

    stackchan.addModifier(std::make_unique<BreathModifier>());
    blink_modifier_id_ = stackchan.addModifier(std::make_unique<BlinkModifier>());
    stackchan.addModifier(std::make_unique<HeadPetModifier>());
    stackchan.addModifier(std::make_unique<ImuEventModifier>());

    preview_image_ = lv_image_create(lv_screen_active());
    lv_obj_set_size(preview_image_, 320, 240);
    lv_obj_align(preview_image_, LV_ALIGN_CENTER, 0, 0);
    lv_obj_add_flag(preview_image_, LV_OBJ_FLAG_HIDDEN);

    // GetHAL().startStackChanAutoUpdate(24);

    auto config        = hal_bridge::get_xiaozhi_config();
    idle_motion_level_ = config.idleRandomMovementLevel;
    ESP_LOGI(TAG, "Avatar created: idle_motion_level=%d (0=Off)", static_cast<int>(idle_motion_level_));
}

void StackChanAvatarDisplay::LvglLock()
{
    if (!Lock(30000)) {
        ESP_LOGE("Display", "Failed to lock display");
    }
}

void StackChanAvatarDisplay::LvglUnlock()
{
    Unlock();
}

void StackChanAvatarDisplay::CreateIdleMotionModifier()
{
    auto& stackchan = GetStackChan();
    ESP_LOGI(TAG, "CreateIdleMotionModifier: level=%d (0=Off)", static_cast<int>(idle_motion_level_));

    switch (idle_motion_level_) {
        case 0:
            idle_motion_modifier_id_ = -1;
            return;
        case 1:
            idle_motion_modifier_id_ = stackchan.addModifier(std::make_unique<IdleMotionModifier>(8000, 12000));
            return;
        case 3:
            idle_motion_modifier_id_ = stackchan.addModifier(std::make_unique<IdleMotionModifier>(2000, 4000));
            return;
        case 2:
        default:
            idle_motion_modifier_id_ = stackchan.addModifier(std::make_unique<IdleMotionModifier>());
            return;
    }
}

namespace {
struct EmotionName
{
    const char* name;
    Emotion emotion;
};

// 服务端 core/utils/textUtils.py 的 EMOJI_MAP 一共 21 个情绪串，
// 外加协议/旧固件用过的别名，全部在这里落地，避免落到 Neutral。
// 新增情绪串时两边要一起改（服务端白名单见 prompt_manager.py 的 EMOJI_List）。
constexpr EmotionName kEmotionNames[] = {
    {"neutral", Emotion::Neutral},
    {"relaxed", Emotion::Neutral},
    {"happy", Emotion::Happy},
    {"loving", Emotion::Happy},
    {"winking", Emotion::Happy},
    {"kissy", Emotion::Happy},
    {"confident", Emotion::Happy},
    {"cool", Emotion::Happy},
    {"delicious", Emotion::Happy},
    {"laughing", Emotion::Laugh},
    {"funny", Emotion::Laugh},
    {"silly", Emotion::Laugh},
    {"surprised", Emotion::Surprised},
    {"surprise", Emotion::Surprised},
    {"shocked", Emotion::Surprised},
    {"embarrassed", Emotion::Surprised},
    {"angry", Emotion::Angry},
    {"sad", Emotion::Sad},
    {"crying", Emotion::Sad},
    {"sleepy", Emotion::Sleepy},
    {"thinking", Emotion::Doubt},
    {"confused", Emotion::Doubt},
    {"doubtful", Emotion::Doubt},
    {"dizzy", Emotion::Dizzy},
};

bool TryMapEmotionName(const char* name, Emotion& mapped)
{
    for (const auto& entry : kEmotionNames) {
        if (strcmp(name, entry.name) == 0) {
            mapped = entry.emotion;
            return true;
        }
    }
    return false;
}
}  // namespace

void StackChanAvatarDisplay::SetEmotion(const char* emotion)
{
    auto& stackchan = GetStackChan();

    if (!stackchan.hasAvatar() || !emotion) {
        // 设了但没画出来：这类"日志里没有出现"的情况也要留痕，
        // 否则排查时只会看到"我明明设过表情，屏幕上却没变"。
        ESP_LOGW(TAG, "SetEmotion(%s) ignored: %s", emotion ? emotion : "null",
                 stackchan.hasAvatar() ? "null name" : "no avatar");
        return;
    }

    DisplayLockGuard lock(this);

    auto& avatar = stackchan.avatar();

    // 情绪一变就离开睡眠态。否则 is_sleeping_ 一旦置位会永远为真，
    // 之后每次 SetStatus 都会把屏幕文字清空。
    const bool was_sleeping = is_sleeping_;
    is_sleeping_ = false;

    Emotion mapped = Emotion::Neutral;
    if (!TryMapEmotionName(emotion, mapped)) {
        ESP_LOGW(TAG, "Unknown emotion: %s, using NEUTRAL", emotion);
    }
    ESP_LOGI(TAG, "SetEmotion: %s -> %d", emotion, static_cast<int>(mapped));

    if (mapped == Emotion::Sleepy) {
        is_sleeping_ = true;
        // Zzz 现在由 ZzzDecorator 画，不再占用对话气泡（气泡会覆盖聊天内容）

        // Stop idle motion
        ESP_LOGW(TAG, "Stop idle motion");
        if (idle_motion_modifier_id_ >= 0) {
            stackchan.removeModifier(idle_motion_modifier_id_);
            idle_motion_modifier_id_ = -1;
            stackchan.removeModifier(idle_expression_modifier_id_);
            idle_expression_modifier_id_ = -1;
        }

        // Return to default pose
        auto& motion = GetStackChan().motion();
        motion.pitchServo().moveWithSpeed(0, 80);
    }

    avatar.setEmotion(mapped);
    UpdateEmotionDecorators(mapped);

    // 从困态醒来：困脸会把头压到底（pitch 0 → 夹到 3°），离开困态时没人抬它，
    // 结果就是"睡醒后一直低着头"。这里补一次回常态位。
    if (was_sleeping && mapped != Emotion::Sleepy) {
        ESP_LOGI(TAG, "wake from sleepy, pose back to %d", chan_pose::kPitchHome);
        stackchan.motion().moveWithSpeed(0, chan_pose::kPitchHome, 300);
    }

    // Resync blink modifier base eye weights
    auto blink_modifier = static_cast<BlinkModifier*>(stackchan.getModifier(blink_modifier_id_));
    if (blink_modifier) {
        blink_modifier->resyncEyeWeights();
    }
}

// 情绪配饰：生气→青筋、难过→眼泪、疑惑→问号、惊讶→感叹号、
// 开心/大笑→爱心、困→Zzz。其他情绪一律收掉。
// 只在情绪真正变化时才重建，否则每次 SetEmotion 都会重播一遍弹出/出现动画。
void StackChanAvatarDisplay::UpdateEmotionDecorators(Emotion emotion)
{
    auto& avatar  = GetStackChan().avatar();
    const int key = static_cast<int>(emotion);

    // 装饰器可能被 clearDecorators()（例如配网流程）清掉，id 还在但对象没了
    if (!emotion_decorator_ids_.empty() && avatar.getDecorator(emotion_decorator_ids_.front()) == nullptr) {
        emotion_decorator_ids_.clear();
        emotion_decorator_key_ = -1;
    }

    if (key == emotion_decorator_key_) {
        return;
    }

    for (int id : emotion_decorator_ids_) {
        avatar.removeDecorator(id);
    }
    emotion_decorator_ids_.clear();
    emotion_decorator_key_ = key;

    auto add = [&](std::unique_ptr<Decorator> decorator) {
        emotion_decorator_ids_.push_back(avatar.addDecorator(std::move(decorator)));
    };

    switch (emotion) {
        case Emotion::Angry:
            add(std::make_unique<AngryDecorator>(lv_screen_active(), 0));
            break;
        case Emotion::Sad:
            add(std::make_unique<TearDecorator>(lv_screen_active(), 0));
            break;
        case Emotion::Doubt:
            add(std::make_unique<SymbolDecorator>(lv_screen_active(), &decorator_question));
            break;
        case Emotion::Surprised:
            add(std::make_unique<SymbolDecorator>(lv_screen_active(), &decorator_exclam));
            break;
        case Emotion::Happy:
        case Emotion::Laugh:
            add(std::make_unique<HeartDecorator>(lv_screen_active(), 0, 500));
            break;
        case Emotion::Sleepy:
            add(std::make_unique<ZzzDecorator>(lv_screen_active(), 0));
            break;
        default:
            break;
    }
}

void StackChanAvatarDisplay::SetChatMessage(const char* role, const char* content)
{
    if (!setup_ui_called_) {
        ESP_LOGW(TAG, "SetChatMessage('%s', '%s') called before SetupUI() - message will be lost!", role, content);
    }

    auto& stackchan = GetStackChan();
    if (!stackchan.hasAvatar()) {
        return;
    }

    // ESP_LOGE(TAG, "SetChatMessage: role=%s, content=%s", role ? role : "null", content ? content : "null");

    DisplayLockGuard lock(this);

    if (strcmp(role, "system") == 0) {
        stackchan.avatar().setSpeech(content);
    } else if (strcmp(role, "assistant") == 0) {
        stackchan.avatar().setSpeech(content);
    }
}

void StackChanAvatarDisplay::ClearChatMessages()
{
    auto& stackchan = GetStackChan();
    if (!stackchan.hasAvatar()) {
        return;
    }

    DisplayLockGuard lock(this);

    stackchan.avatar().clearSpeech();

    ESP_LOGI(TAG, "Chat messages cleared");
}

void StackChanAvatarDisplay::SetPreviewImage(std::unique_ptr<LvglImage> image)
{
    DisplayLockGuard lock(this);
    if (preview_image_ == nullptr) {
        return;
    }

    if (image == nullptr) {
        esp_timer_stop(preview_timer_);
        lv_obj_add_flag(preview_image_, LV_OBJ_FLAG_HIDDEN);
        preview_image_cached_.reset();
        return;
    }

    preview_image_cached_ = std::move(image);
    auto img_dsc          = preview_image_cached_->image_dsc();
    // Set image source and show preview image
    lv_image_set_src(preview_image_, img_dsc);
    if (img_dsc->header.w > 0 && img_dsc->header.h > 0) {
        // Scale to fit width
        lv_image_set_scale(preview_image_, 256 * width_ / img_dsc->header.w);
    }

    lv_obj_remove_flag(preview_image_, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(preview_image_);
    esp_timer_stop(preview_timer_);
    ESP_ERROR_CHECK(esp_timer_start_once(preview_timer_, 6000 * 1000));
}

void StackChanAvatarDisplay::UpdateStatusBar(bool update_all)
{
}

void StackChanAvatarDisplay::SetTheme(Theme* theme)
{
    ESP_LOGI(TAG, "SetTheme: %s", theme->name().c_str());

    auto& stackchan = GetStackChan();
    if (!stackchan.hasAvatar()) {
        ESP_LOGE(TAG, "Avatar is invalid");
        return;
    }

    DisplayLockGuard lock(this);

    auto lvgl_theme = static_cast<LvglTheme*>(theme);
    auto text_font  = lvgl_theme->text_font()->font();

    stackchan.avatar().setSpeechTextFont((void*)text_font);
}

#include <hal/board/hal_bridge.h>
static bool _is_xiaozhi_ready = false;
static bool _is_xiaozhi_idle  = false;
bool hal_bridge::is_xiaozhi_ready()
{
    return _is_xiaozhi_ready;
}
bool hal_bridge::is_xiaozhi_idle()
{
    return _is_xiaozhi_idle;
}

void StackChanAvatarDisplay::SetStatus(const char* status)
{
    // ESP_LOGE(TAG, "SetStatus: %s", status);

    auto& stackchan = GetStackChan();
    if (!stackchan.hasAvatar()) {
        ESP_LOGE(TAG, "Avatar is invalid");
        return;
    }

    auto& avatar = stackchan.avatar();
    auto& motion = stackchan.motion();

    DisplayLockGuard lock(this);

    bool is_idle      = false;
    bool is_listening = false;

    if (strcmp(status, Lang::Strings::LISTENING) == 0) {
        if (speaking_modifier_id_ >= 0) {
            // Start speaking
            stackchan.removeModifier(speaking_modifier_id_);
            avatar.mouth().setWeight(0);
            speaking_modifier_id_ = -1;
        }

        // TTS 结束后进入聆听，清掉上一轮的气泡。
        // 气泡是 LV_LABEL_LONG_MODE_SCROLL_CIRCULAR，只要可见就会一直循环滚动；
        // 自动对话模式又不会回到 idle，所以必须在这里清。
        avatar.clearSpeech();

        // 进入聆听 = 用户要说话，行为表演让位给语音
        behavior_runtime_on_voice_activity();

        // 聆听：单颗 LED 0（用户实验：光会从壳体扩散，单颗即可）
        GetHAL().setRgbColor(0, 0, 50, 0);
        GetHAL().refreshRgb();

    } else if (strcmp(status, Lang::Strings::STANDBY) == 0) {
        _is_xiaozhi_ready = true;

        if (speaking_modifier_id_ >= 0) {
            // Stop speaking
            stackchan.removeModifier(speaking_modifier_id_);
            avatar.mouth().setWeight(0);
            speaking_modifier_id_ = -1;
        }

        is_idle = true;

        // 待机：单颗 LED 0 熄灭（遥控紫灯会在语音结束后由 teleop 恢复）
        GetHAL().setRgbColor(0, 0, 0, 0);
        GetHAL().refreshRgb();

    } else if (strcmp(status, Lang::Strings::SPEAKING) == 0) {
        if (speaking_modifier_id_ < 0) {
            speaking_modifier_id_ = stackchan.addModifier(std::make_unique<SpeakingModifier>(0, 180, false));
        }

        // 说话：单颗 LED 0 蓝色
        GetHAL().setRgbColor(0, 0, 0, 50);
        GetHAL().refreshRgb();
    } else {
        avatar.setSpeech(status);
    }

    if (is_idle) {
        // Start idle motion
        ESP_LOGW(TAG, "Start idle (level=%d, 0=Off)", static_cast<int>(idle_motion_level_));
        if (idle_motion_modifier_id_ < 0) {
            if (idle_motion_level_ > 0) {
                CreateIdleMotionModifier();
            }
            idle_expression_modifier_id_ = stackchan.addModifier(std::make_unique<IdleExpressionModifier>());
        }

        _is_xiaozhi_idle = true;
    } else {
        // Stop idle motion
        ESP_LOGW(TAG, "Stop idle motion");
        if (idle_motion_modifier_id_ >= 0) {
            stackchan.removeModifier(idle_motion_modifier_id_);
            idle_motion_modifier_id_ = -1;
            stackchan.removeModifier(idle_expression_modifier_id_);
            idle_expression_modifier_id_ = -1;
        }

        // if (!is_listening) {
        //     // Return to default pose
        //     motion.pitchServo().moveWithSpeed(200, 350);
        //     motion.yawServo().moveWithSpeed(0, 350);
        // }

        _is_xiaozhi_idle = false;
    }

    // Clear sleep state
    if (is_sleeping_) {
        avatar.setSpeech("");
    }
}

void StackChanAvatarDisplay::ShowNotification(const char* notification, int duration_ms)
{
}
