/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "countdown_overlay.h"
#include <cmath>
#include <cstdio>
#include <esp_log.h>
#include <lvgl_theme.h>

LV_FONT_DECLARE(BUILTIN_TEXT_FONT);

LV_IMAGE_DECLARE(default_bomb);

using namespace uitk;
using namespace uitk::lvgl_cpp;
using namespace stackchan::avatar;

namespace {

// 设备的脸是「黑底 + 暖白五官」，覆盖层必须同底，切换时才不跳。
// 这里用纯黑：早先写 0x0A0A0A，比脸的背景亮一档，屏幕上能看出"一条灰带"。
const lv_color_t kBlack   = lv_color_hex(0x000000);
const lv_color_t kWarm    = lv_color_hex(0xF2E8DC);  // 暖白文字
const lv_color_t kClay    = lv_color_hex(0xB85C38);  // 陶土强调
const lv_color_t kAmber   = lv_color_hex(0xD98B2B);  // 火焰中段
const lv_color_t kEmber   = lv_color_hex(0x7A3B22);  // 火焰外晕
const lv_color_t kRed     = lv_color_hex(0xC4453A);
const lv_color_t kAsh     = lv_color_hex(0x2A2420);  // 已烧掉的部分：只剩灰痕

constexpr int kSegments   = 24;
constexpr int kBandHeight = 68;  // 24px 时间 + 20px 中文各一行，留 6px 余量

// 长按抽屉的几何：和 hal.cpp 的坐标判定共用，改动时只需改这里。
// 130 起是因为信息带在 172，抽屉要盖住它；上方 130px 留给脸，点脸 = 关闭。
constexpr int kPanelTop  = 124;
constexpr int kPanelH    = 116;
constexpr int kBtnY      = 158;  // 屏幕坐标
constexpr int kBtnH      = 46;
constexpr int kBtnW      = 140;
constexpr int kBtnLeftX  = 14;
constexpr int kBtnRightX = 166;
constexpr int kContinueY = 208;  // 「继续专注」那一行，往下整条都算退出

// 引线几何：从左往右烧，末段抬起来钻进炸弹脖口
constexpr int kFuseY      = 186;
constexpr int kFuseX0     = 22;
constexpr int kFuseX1     = 253;    // 终点落在炸弹脖口里，被贴图盖住
constexpr int kFuseRise   = 18;     // 末段抬升高度（与脖口 32° 的朝向对齐）
constexpr float kRiseFrom = 0.78f;  // 从 78% 开始抬

// 炸弹贴图 72x72：弹体圆心在 (41,44)，脖口在 (22.5,14.3)
// 放在 (231,154) → 弹体圆心 (272,198)、脖口 (253.5,168.3)，与引线终点重合
constexpr int kBombImgX = 231;
constexpr int kBombImgY = 154;

/**
 * 取屏幕当前用的字体：主题里的字体可能比 BUILTIN_TEXT_FONT 覆盖更全
 * （资产系统会给主题换字体）。取不到就退回内置字体。
 */
const lv_font_t* resolve_text_font()
{
    auto& manager = LvglThemeManager::GetInstance();
    for (const char* name : {"dark", "light"}) {
        auto* theme = manager.GetTheme(name);
        if (theme == nullptr) {
            continue;
        }
        auto font = theme->GetTextFont();
        if (font != nullptr && font->font() != nullptr) {
            return font->font();
        }
    }
    return &BUILTIN_TEXT_FONT;
}

/** 字体里有没有这个字。BUILTIN 是「常用字子集」，随便写中文会整段渲染不出来。 */
bool font_has(const lv_font_t* font, uint32_t ch)
{
    lv_font_glyph_dsc_t dsc;
    return font != nullptr && lv_font_get_glyph_dsc(font, &dsc, ch, 0);
}

void format_mmss(char* buf, size_t len, int ms)
{
    if (ms < 0) {
        ms = 0;
    }
    int total = (ms + 999) / 1000;  // ceil：不允许提前显示 00:00
    snprintf(buf, len, "%02d:%02d", total / 60, total % 60);
}

}  // namespace

DefaultCountdownOverlay::DefaultCountdownOverlay(lv_obj_t* parent, int width, int height,
                                                 const lv_font_t* text_font)
    : _w(width), _h(height), _font(text_font ? text_font : resolve_text_font())
{
    build();
}

DefaultCountdownOverlay::~DefaultCountdownOverlay()
{
    _panel_continue.reset();
    _panel_abandon_text.reset();
    _panel_abandon.reset();
    _panel_done_text.reset();
    _panel_done.reset();
    _panel_time.reset();
    _panel_label.reset();
    _panel.reset();
    _bar_fill.reset();
    _band_task.reset();
    _band_label.reset();
    _band_time.reset();
    _band.reset();
    _fuse_hint.reset();
    _fuse_time.reset();
    _bomb.reset();
    _fuse.reset();
    _root.reset();
}

void DefaultCountdownOverlay::build()
{
    _root = std::make_unique<Container>(lv_screen_active());
    _root->setSize(_w, _h);
    _root->setPos(0, 0);
    _root->setBgOpa(0);
    _root->setBorderWidth(0);
    _root->setPadding(0, 0, 0, 0);
    _root->removeFlag(LV_OBJ_FLAG_SCROLLABLE);
    _root->setHidden(true);

    // ---------- 信息带（黑底，与脸同色） ----------
    _band = std::make_unique<Container>(_root->get());
    _band->setSize(_w, kBandHeight);
    _band->setPos(0, _h - kBandHeight);
    _band->setRadius(0);
    _band->setBorderWidth(0);
    _band->setBgColor(kBlack);
    _band->setBgOpa(LV_OPA_COVER);
    // 内边距必须为 0：子对象用绝对坐标定位，padding 会把它们整体往下推 12px，
    // 20px 的中文字体就会顶出 62px 的信息带（实测任务名被裁掉半行）
    _band->setPadding(0, 0, 0, 0);
    _band->removeFlag(LV_OBJ_FLAG_SCROLLABLE);

    _band_time = std::make_unique<Label>(_band->get());
    _band_time->setText("25:00");
    _band_time->setTextColor(kWarm);
    _band_time->setTextFont(&lv_font_montserrat_24);
    _band_time->setPos(14, 2);

    _band_label = std::make_unique<Label>(_band->get());
    _band_label->setText("FOCUS");
    _band_label->setTextColor(kClay);
    _band_label->setTextFont(_font);
    _band_label->setPos(96, 6);

    _band_task = std::make_unique<Label>(_band->get());
    _band_task->setText("");
    _band_task->setTextColor(kWarm);
    _band_task->setTextFont(_font);
    _band_task->setWidth(_w - 28);
    _band_task->setLongMode(LV_LABEL_LONG_MODE_DOTS);
    _band_task->setPos(14, 34);

    // 内置字体只有常用字子集：任务名多半渲染不出来，宁可整行不显示也不出半个字
    _cjk_ok = font_has(_font, 0x4E13 /* 专 */) && font_has(_font, 0x6CE8 /* 注 */);
    if (!_cjk_ok) {
        ESP_LOGW("Countdown", "text font lacks CJK glyphs, falling back to ASCII labels");
    }

    // 进度：只有"已经走过的部分"，没有底轨，也没有分隔线——整块融进黑底。
    // 早先是「底轨容器 + 填充子容器」，填充层宽度设了但从没渲染出来（屏幕上只有一条
    // 静止的灰轨）。现在把填充直接挂在信息带上，省掉一层父子关系。
    _bar_fill = std::make_unique<Container>(_band->get());
    _bar_fill->setSize(0, 3);
    _bar_fill->setPos(0, kBandHeight - 3);
    _bar_fill->setRadius(0);
    _bar_fill->setBorderWidth(0);
    _bar_fill->setBgColor(kClay);
    _bar_fill->setBgOpa(LV_OPA_COVER);

    // ---------- 引线 + 炸弹（全屏，黑底） ----------
    _fuse = std::make_unique<Container>(_root->get());
    _fuse->setSize(_w, _h);
    _fuse->setPos(0, 0);
    _fuse->setRadius(0);
    _fuse->setBorderWidth(0);
    _fuse->setBgColor(kBlack);
    _fuse->setBgOpa(LV_OPA_COVER);
    _fuse->setPadding(0, 0, 0, 0);
    _fuse->removeFlag(LV_OBJ_FLAG_SCROLLABLE);
    _fuse->setHidden(true);

    _fuse_time = std::make_unique<Label>(_fuse->get());
    _fuse_time->setText("05:00");
    _fuse_time->setTextColor(kWarm);
    _fuse_time->setTextFont(&lv_font_montserrat_40);
    _fuse_time->align(LV_ALIGN_CENTER, -20, -34);

    _fuse_hint = std::make_unique<Label>(_fuse->get());
    _fuse_hint->setText("");
    _fuse_hint->setTextColor(kClay);
    _fuse_hint->setTextFont(_font);
    _fuse_hint->align(LV_ALIGN_CENTER, -20, 14);

    // 引线折线：24 段。主体是缓和波形，末段抬起，插进炸弹脖口
    _pts.clear();
    for (int i = 0; i <= kSegments; i++) {
        const float t = (float)i / kSegments;
        lv_point_precise_t p;
        p.x = kFuseX0 + (float)(kFuseX1 - kFuseX0) * t;
        // 越靠近炸弹波形越平，避免抖动和脖口对不上
        const float wave = 4.5f * sinf(i * 0.9f) * (1.0f - powf(t, 6.0f));
        float rise       = 0.0f;
        if (t > kRiseFrom) {
            const float u = (t - kRiseFrom) / (1.0f - kRiseFrom);
            rise          = -kFuseRise * u * u;
        }
        p.y = kFuseY + wave + rise;
        _pts.push_back(p);
    }

    _line_dim = lv_line_create(_fuse->get());
    lv_line_set_points(_line_dim, _pts.data(), _pts.size());
    lv_obj_set_style_line_width(_line_dim, 3, 0);
    lv_obj_set_style_line_color(_line_dim, kAmber, 0);
    lv_obj_set_style_line_rounded(_line_dim, true, 0);

    _line_glow = lv_line_create(_fuse->get());
    lv_line_set_points(_line_glow, _pts.data(), 1);
    lv_obj_set_style_line_width(_line_glow, 3, 0);
    lv_obj_set_style_line_color(_line_glow, kWarm, 0);
    lv_obj_set_style_line_rounded(_line_glow, true, 0);

    _line_hot = lv_line_create(_fuse->get());
    lv_line_set_points(_line_hot, _pts.data(), 1);
    lv_obj_set_style_line_width(_line_hot, 3, 0);
    lv_obj_set_style_line_color(_line_hot, kAsh, 0);
    lv_obj_set_style_line_width(_line_hot, 2, 0);
    lv_obj_set_style_line_rounded(_line_hot, true, 0);

    // 炸弹：贴图（含弹体、脖口），不带引线；引线从它下面穿过、被盖住，
    // 视觉上就是「插进脖口」。改图不需要重算引线坐标。
    _bomb = std::make_unique<Image>(_fuse->get());
    _bomb->setSrc(&default_bomb);
    _bomb->setPos(kBombImgX, kBombImgY);
    _bomb->removeFlag(LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(_bomb->get(), LV_OBJ_FLAG_CLICKABLE);

    // 火焰：三层同心圆（外晕/中段/白芯）。只在小范围内改尺寸和位置，
    // 8Hz 抖动即可读作「在烧」；不逐帧全屏重绘（会和 I2S/Opus 抢资源）。
    for (int i = 0; i < 3; i++) {
        _flames[i] = lv_obj_create(_fuse->get());
        lv_obj_set_style_radius(_flames[i], LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_border_width(_flames[i], 0, 0);
    }
    lv_obj_set_style_bg_color(_flames[0], kEmber, 0);
    lv_obj_set_style_bg_opa(_flames[0], LV_OPA_60, 0);
    lv_obj_set_style_bg_color(_flames[1], kAmber, 0);
    lv_obj_set_style_bg_opa(_flames[1], LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(_flames[2], kWarm, 0);
    lv_obj_set_style_bg_opa(_flames[2], LV_OPA_COVER, 0);
    for (int i = 0; i < kSparks; i++) {
        _sparks[i] = lv_obj_create(_fuse->get());
        lv_obj_set_style_radius(_sparks[i], LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_border_width(_sparks[i], 0, 0);
        lv_obj_set_style_bg_color(_sparks[i], kAmber, 0);
        lv_obj_set_style_bg_opa(_sparks[i], LV_OPA_TRANSP, 0);
        _spark_life[i] = 0;
        _spark_x[i]    = 0;
        _spark_y[i]    = 0;
    }
    setFlame(_pts[0].x, _pts[0].y, 0);

    // ---------- 长按抽屉 ----------
    // 盖住信息带（y=130 起），上方留出脸：点脸 = 关闭，符合"点空白退出"的习惯。
    _panel = std::make_unique<Container>(_root->get());
    _panel->setSize(_w, kPanelH);
    _panel->setPos(0, kPanelTop);
    _panel->setRadius(0);
    _panel->setBorderWidth(0);
    _panel->setBgColor(kBlack);
    _panel->setBgOpa(LV_OPA_COVER);
    _panel->setPadding(0, 0, 0, 0);
    _panel->removeFlag(LV_OBJ_FLAG_SCROLLABLE);
    _panel->setHidden(true);

    _panel_label = std::make_unique<Label>(_panel->get());
    _panel_label->setText(_cjk_ok ? "专注" : "FOCUS");
    _panel_label->setTextColor(kClay);
    _panel_label->setTextFont(_font);
    _panel_label->setPos(14, 4);

    _panel_time = std::make_unique<Label>(_panel->get());
    _panel_time->setText("25:00");
    _panel_time->setTextColor(kWarm);
    _panel_time->setTextFont(_font);
    _panel_time->setPos(62, 4);

    _panel_done = std::make_unique<Container>(_panel->get());
    _panel_done->setSize(kBtnW, kBtnH);
    _panel_done->setPos(kBtnLeftX, kBtnY - kPanelTop);
    _panel_done->setRadius(10);
    _panel_done->setBorderWidth(0);
    _panel_done->setBgColor(kClay);
    _panel_done->setBgOpa(LV_OPA_COVER);

    _panel_done_text = std::make_unique<Label>(_panel_done->get());
    _panel_done_text->setText(_cjk_ok ? "完成" : "DONE");
    _panel_done_text->setTextColor(kBlack);
    _panel_done_text->setTextFont(_font);
    _panel_done_text->align(LV_ALIGN_CENTER, 0, 0);

    _panel_abandon = std::make_unique<Container>(_panel->get());
    _panel_abandon->setSize(kBtnW, kBtnH);
    _panel_abandon->setPos(kBtnRightX, kBtnY - kPanelTop);
    _panel_abandon->setRadius(10);
    _panel_abandon->setBorderWidth(1);
    _panel_abandon->setBorderColor(lv_color_hex(0x5A524B));
    _panel_abandon->setBgOpa(LV_OPA_TRANSP);

    _panel_abandon_text = std::make_unique<Label>(_panel_abandon->get());
    _panel_abandon_text->setText(_cjk_ok ? "放弃任务" : "ABANDON");
    _panel_abandon_text->setTextColor(kWarm);
    _panel_abandon_text->setTextFont(_font);
    _panel_abandon_text->align(LV_ALIGN_CENTER, 0, 0);

    // 第三出口：不占按钮位，避免和两个操作并列后又被当成"取消面板"
    _panel_continue = std::make_unique<Label>(_panel->get());
    _panel_continue->setText(_cjk_ok ? "继续专注" : "KEEP GOING");
    _panel_continue->setTextColor(lv_color_hex(0x8A8078));
    _panel_continue->setTextFont(_font);
    _panel_continue->align(LV_ALIGN_TOP_MID, 0, kContinueY - kPanelTop);
}

void DefaultCountdownOverlay::setFlame(float x, float y, uint32_t tick)
{
    // 用整数扰动代替随机数：可复现，渲染器出图时不会每次都变
    const int wob_x = (int)(tick % 3) - 1;
    const int wob_y = (int)((tick / 3) % 3) - 1;

    const int radius[3] = {9, 6, 3};
    const int dx[3]     = {wob_x, -wob_x, wob_x / 2};
    const int dy[3]     = {wob_y, wob_y, -wob_y};

    for (int i = 0; i < 3; i++) {
        int d = radius[i] * 2;
        lv_obj_set_size(_flames[i], d, d);
        lv_obj_set_pos(_flames[i], (int)x - radius[i] + dx[i], (int)y - radius[i] + dy[i]);
    }

    // 火星：每 tick 推进 1 格，生命周期 5 格；用 tick 取模决定何时生成，
    // 保证渲染器出图可复现（不用随机数）
    for (int i = 0; i < kSparks; i++) {
        if (_spark_life[i] <= 0) {
            if ((tick + i * 2) % 5 == 0) {
                _spark_life[i] = 5;
                _spark_x[i]    = (int)x + (int)((tick + i) % 7) - 3;
                _spark_y[i]    = (int)y - 4;
            } else {
                lv_obj_set_style_bg_opa(_sparks[i], LV_OPA_TRANSP, 0);
                continue;
            }
        }
        _spark_life[i]--;
        _spark_y[i] -= 3;
        int age = 5 - _spark_life[i];
        int d   = age < 3 ? 3 : 2;
        lv_obj_set_size(_sparks[i], d, d);
        lv_obj_set_pos(_sparks[i], _spark_x[i], _spark_y[i]);
        lv_obj_set_style_bg_opa(_sparks[i], _spark_life[i] > 2 ? LV_OPA_80 : LV_OPA_40, 0);
    }
}

void DefaultCountdownOverlay::setBandVisible(bool visible)
{
    _band->setHidden(!visible);
}

void DefaultCountdownOverlay::setFuseVisible(bool visible)
{
    _fuse->setHidden(!visible);
}

void DefaultCountdownOverlay::setFuseProgress(int progress)
{
    if (progress < 0) {
        progress = 0;
    }
    if (progress > 100) {
        progress = 100;
    }
    int burnt = (kSegments * progress) / 100;
    if (burnt > kSegments) {
        burnt = kSegments;
    }
    // 未燃段=琥珀（最显眼），已烧段=灰痕（细一号），火焰身后 2 段=白热
    lv_line_set_points(_line_dim, &_pts[burnt], kSegments - burnt + 1);
    lv_line_set_points(_line_hot, _pts.data(), burnt + 1);
    int start = burnt > 2 ? burnt - 2 : 0;
    lv_line_set_points(_line_glow, &_pts[start], burnt - start + 1);
    setFlame(_pts[burnt].x, _pts[burnt].y, _flame_tick++);
}

void DefaultCountdownOverlay::hide()
{
    hidePanel();
    _root->setHidden(true);
}

void DefaultCountdownOverlay::showPanel(const char* label, int remaining_ms)
{
    _root->setHidden(false);
    char buf[16];
    const bool overtime = remaining_ms < 0;
    if (overtime) {
        // 超时态打开抽屉：标题要显示 +01:23，而不是 00:00
        const int over = (-remaining_ms + 999) / 1000;
        snprintf(buf, sizeof(buf), "+%02d:%02d", over / 60, over % 60);
    } else {
        format_mmss(buf, sizeof(buf), remaining_ms);
    }
    _panel_label->setText(_cjk_ok ? (label ? label : "专注") : "FOCUS");
    _panel_label->setTextColor(overtime ? kRed : kClay);
    _panel_time->setTextColor(overtime ? kRed : kWarm);
    _panel_time->setPos(_cjk_ok ? 62 : 96, 4);
    _panel_time->setText(buf);
    _panel->setHidden(false);
}

void DefaultCountdownOverlay::hidePanel()
{
    if (_panel) {
        _panel->setHidden(true);
    }
}

bool DefaultCountdownOverlay::panelVisible() const
{
    return _panel && !lv_obj_has_flag(_panel->get(), LV_OBJ_FLAG_HIDDEN);
}

DefaultCountdownOverlay::PanelHit DefaultCountdownOverlay::panelHitTest(int x, int y) const
{
    if (!panelVisible()) {
        return PanelHit::None;
    }
    // 抽屉上方（脸的区域）：关闭。这是"点空白退出"的肌肉记忆路径。
    if (y < kPanelTop) {
        return PanelHit::Continue;
    }
    if (y >= kBtnY && y < kBtnY + kBtnH) {
        if (x >= kBtnLeftX && x < kBtnLeftX + kBtnW) {
            return PanelHit::Complete;
        }
        if (x >= kBtnRightX && x < kBtnRightX + kBtnW) {
            return PanelHit::Abandon;
        }
    }
    // 「继续专注」整条都算：小字只有 80px 宽，但这里给足容错
    if (y >= kContinueY) {
        return PanelHit::Continue;
    }
    // 抽屉里的空白（比如标题行左右两侧）：什么都不做，避免误触
    return PanelHit::None;
}

void DefaultCountdownOverlay::showBand(int remaining_ms, const char* label, const char* task, int progress)
{
    _root->setHidden(false);
    setFuseVisible(false);
    setBandVisible(true);

    char buf[16];
    format_mmss(buf, sizeof(buf), remaining_ms);
    _band_time->setText(buf);
    _band_time->setTextColor(kWarm);
    _band_label->setText(_cjk_ok ? (label ? label : "专注") : "FOCUS");
    _band_label->setTextColor(kClay);
    _band_label->setPos(_cjk_ok ? 96 : 104, 6);
    _band_task->setText(_cjk_ok && task ? task : "");

    int p = progress < 0 ? 0 : (progress > 100 ? 100 : progress);
    _bar_fill->setWidth((_w * p) / 100);
    _bar_fill->setBgColor(kClay);
}

void DefaultCountdownOverlay::showFuse(int remaining_ms, int total_ms, const char* subtitle)
{
    _root->setHidden(false);
    setBandVisible(false);
    setFuseVisible(true);

    char buf[16];
    format_mmss(buf, sizeof(buf), remaining_ms);
    const int burnt = total_ms > 0 ? 100 - (remaining_ms * 100) / total_ms : 100;
    _fuse_time->setText(buf);
    _fuse_time->setTextColor(burnt >= 100 ? kRed : kWarm);
    // 副标题是任务名（空就不显示）。早先放"最后 N 分钟"：大字已经在倒计时，
    // 而且 20% 窗口不足一分钟时那句是错的。
    _fuse_hint->setText(subtitle ? subtitle : "");
    setFuseProgress(burnt);
}

void DefaultCountdownOverlay::showOvertime(int overtime_ms, const char* label)
{
    _root->setHidden(false);
    setFuseVisible(false);
    setBandVisible(true);

    char buf[20];
    int total = (overtime_ms + 999) / 1000;
    snprintf(buf, sizeof(buf), "+%02d:%02d", total / 60, total % 60);
    _band_time->setText(buf);
    _band_time->setTextColor(kRed);
    _band_label->setText(_cjk_ok ? (label ? label : "超时") : "OVER");
    _band_label->setTextColor(kRed);
    _band_label->setPos(_cjk_ok ? 110 : 104, 6);
    _band_task->setText("");
    _bar_fill->setWidth(_w);
    _bar_fill->setBgColor(kRed);
}
