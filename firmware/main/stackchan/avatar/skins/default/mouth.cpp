/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "default.h"

#include <math.h>

using namespace uitk;
using namespace uitk::lvgl_cpp;
using namespace stackchan::avatar;

// 嘴再往下一点：原来 26 太贴近眼睛
static const Vector2i _mouth_pos        = Vector2i(0, 38);
static const Vector2i _mouth_min_offset = Vector2i(-16, -16);
static const Vector2i _mouth_max_offset = Vector2i(16, 16);
static const Vector2i _mouth_min_size   = Vector2i(90, 6);
static const Vector2i _mouth_max_size   = Vector2i(60, 50);
static const int _mouth_min_radius      = 0;
static const int _mouth_max_radius      = 16;

// 唇线用 lv_arc 画圆弧：以 90°（下方）或 270°（上方）为中心。
// 半张角越大，弧越深、越宽。
static const int _lip_r      = 44;
static const int _lip_border = 6;

static lv_obj_t* make_box(lv_obj_t* parent, lv_color_t color, int w, int h, int radius)
{
    lv_obj_t* o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_bg_color(o, color, 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(o, radius, 0);
    lv_obj_set_size(o, w, h);
    return o;
}

DefaultMouth::DefaultMouth(lv_obj_t* parent, lv_color_t primaryColor, lv_color_t secondaryColor)
{
    _mouth = std::make_unique<Container>(parent);
    _mouth->setAlign(LV_ALIGN_CENTER);
    _mouth->setBorderWidth(0);
    _mouth->setBgColor(primaryColor);
    _mouth->removeFlag(LV_OBJ_FLAG_SCROLLABLE);

    // 弧线唇（微笑 / 难过）
    _lip = lv_arc_create(parent);
    lv_obj_remove_style_all(_lip);
    lv_obj_remove_flag(_lip, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_arc_opa(_lip, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_arc_opa(_lip, LV_OPA_TRANSP, LV_PART_KNOB);
    lv_obj_set_style_bg_opa(_lip, LV_OPA_TRANSP, LV_PART_KNOB);
    lv_obj_set_style_arc_color(_lip, primaryColor, LV_PART_INDICATOR);
    lv_obj_set_style_arc_width(_lip, _lip_border, LV_PART_INDICATOR);
    lv_obj_set_size(_lip, _lip_r * 2, _lip_r * 2);
    lv_arc_set_bg_angles(_lip, 0, 360);

    // 平嘴（疑惑）
    _flat = make_box(parent, primaryColor, 72, _lip_border, _lip_border / 2);

    // D 形嘴的"方半边"：作为口腔的子对象，说话时盖住胶囊的一半，
    // 让开口变成"平顶圆底"（开心）或"平底圆顶"（难过）。
    // 前提：_mouth 的 padding 必须为 0，否则子对象对齐会被内边距推开。
    _mouth->setPadding(0, 0, 0, 0);

    _square_half = lv_obj_create(_mouth->get());
    lv_obj_remove_style_all(_square_half);
    lv_obj_remove_flag(_square_half, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(_square_half, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_bg_color(_square_half, primaryColor, 0);
    lv_obj_set_style_bg_opa(_square_half, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(_square_half, 0, 0);
    lv_obj_add_flag(_square_half, LV_OBJ_FLAG_HIDDEN);

    // 龇牙（生气）：白色底板 + 三条黑色牙缝
    _teeth = make_box(parent, primaryColor, 52, 18, 7);
    for (int i = 0; i < 3; i++) {
        _tooth_gap[i] = make_box(parent, secondaryColor, 4, 18, 1);
    }
    lv_obj_add_flag(_teeth, LV_OBJ_FLAG_HIDDEN);
    for (auto* g : _tooth_gap) {
        lv_obj_add_flag(g, LV_OBJ_FLAG_HIDDEN);
    }

    apply_lip(0);

    setPosition(_position);
    setWeight(0);
    setRotation(0);
}

DefaultMouth::~DefaultMouth()
{
    // 面板销毁时已把子对象一起删掉，这里必须先确认还活着，否则野指针
    for (auto*& g : _tooth_gap) {
        if (g != nullptr && lv_obj_is_valid(g)) {
            lv_obj_delete(g);
        }
        g = nullptr;
    }
    if (_teeth != nullptr && lv_obj_is_valid(_teeth)) {
        lv_obj_delete(_teeth);
    }
    _teeth = nullptr;
    if (_flat != nullptr && lv_obj_is_valid(_flat)) {
        lv_obj_delete(_flat);
    }
    _flat = nullptr;
    if (_lip != nullptr && lv_obj_is_valid(_lip)) {
        lv_obj_delete(_lip);
    }
    _lip = nullptr;
    // _square_half 是 _mouth 的子对象，随容器一起销毁；这里不要再单独 delete，
    // 否则在 LVGL 已经释放过它的情况下 lv_obj_is_valid 可能误判为有效，进而二次释放。
    _square_half = nullptr;
    _mouth.reset();
}

void DefaultMouth::setPosition(const Vector2i& position)
{
    Element::setPosition(position);

    auto pos_x = _mouth_pos.x + map_range(_position.x, -100, 100, _mouth_min_offset.x, _mouth_max_offset.x);
    auto pos_y = _mouth_pos.y + map_range(_position.y, -100, 100, _mouth_min_offset.y, _mouth_max_offset.y);

    _mouth->setPos(pos_x, pos_y);
    apply_lip(_lip_angle);
}

void DefaultMouth::_place_teeth()
{
    lv_obj_align(_teeth, LV_ALIGN_CENTER, 0, _mouth_pos.y);
    for (int i = 0; i < 3; i++) {
        lv_obj_align(_tooth_gap[i], LV_ALIGN_CENTER, -13 + i * 13, _mouth_pos.y);
    }
}

void DefaultMouth::apply_lip(int inner)
{
    _lip_angle = inner;

    if (_teeth_on) {
        _place_teeth();
        return;
    }

    // 半张角：越大弧越深
    const float half_deg = 34.0f + fabsf((float)inner) * 0.38f;
    const int half       = (int)half_deg;

    if (inner == 0) {
        lv_obj_align(_flat, LV_ALIGN_CENTER, 0, _mouth_pos.y);
        _update_visibility();
        return;
    }

    if (inner > 0) {
        // 微笑 ∪：以 90° 为中心，弧在圆的底部
        lv_arc_set_angles(_lip, 90 - half, 90 + half);
        lv_obj_align(_lip, LV_ALIGN_CENTER, 0, _mouth_pos.y - _lip_r);
    } else {
        // 难过 ∩：以 270° 为中心，弧在圆的顶部，圆心要放到嘴下方
        lv_arc_set_angles(_lip, 270 - half, 270 + half);
        lv_obj_align(_lip, LV_ALIGN_CENTER, 0, _mouth_pos.y + _lip_r);
    }

    _update_visibility();
}

// 唯一决定「谁可见」的地方。
// 之前 apply_lip 会无条件把弧线显示出来，而 setPosition 每帧都调用它，
// 于是「张嘴椭圆」和「弧线」会同时在场 —— 就是偶现的两个嘴。
void DefaultMouth::_update_visibility()
{
    const bool talking = (_weight > 8) || (_static_open && !_speaking);

    _mouth->setHidden(!talking);

    auto show = [](lv_obj_t* o, bool on) {
        if (on) {
            lv_obj_remove_flag(o, LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
        }
    };

    // 说话时保留情绪的"签名"，否则九种表情张嘴后长得一模一样：
    //  - 平的一字线让位给口腔（一条线横穿白口腔会很难看）
    //  - 牙齿缝保留：白色的口腔被黑缝切开，就是"张嘴露牙"，angry 说话仍是生气的
    //  - 唇线（弧）不保留：它比口腔还宽，会从两侧露出两个"小角"，看着像画错
    const bool show_curve = !_teeth_on && (_lip_angle != 0) && !talking;
    const bool show_flat  = !talking && !_teeth_on && (_lip_angle == 0);
    const bool show_teeth = !talking && _teeth_on;
    const bool show_gaps  = _teeth_on;

    show(_lip, show_curve);
    show(_flat, show_flat);
    show(_teeth, show_teeth);
    for (auto* g : _tooth_gap) {
        show(g, show_gaps);
    }
}

void DefaultMouth::setEmotion(const Emotion& emotion)
{
    if (getIgnoreEmotion()) {
        return;
    }

    _static_open = false;
    _teeth_on    = false;
    _d_dir       = 0;

    // 换表情时重新判断"是否在说话"：切到 laugh 就该立刻露出它的大笑嘴，
    // 而不是等上一次说话留下的计时过期。
    _speaking       = false;
    _last_open_tick = 0;

    switch (emotion) {
        case Emotion::Happy:
            apply_lip(70);
            _d_dir = 1;
            break;
        case Emotion::Laugh:
            apply_lip(70);
            _d_dir       = 1;
            _static_open = true;
            _open_w      = 54;
            _open_h      = 32;
            break;
        case Emotion::Angry:
            _teeth_on = true;
            apply_lip(0);
            break;
        case Emotion::Sad:
            apply_lip(-80);
            _d_dir = -1;
            break;
        case Emotion::Doubt:     apply_lip(0); break;
        case Emotion::Sleepy:
            apply_lip(0);
            _static_open = true;
            _open_w      = 20;
            _open_h      = 24;
            break;
        case Emotion::Surprised:
            apply_lip(10);
            _static_open = true;
            _open_w      = 26;
            _open_h      = 32;
            break;
        case Emotion::Dizzy:
            apply_lip(-45);
            _d_dir = -1;
            break;
        case Emotion::Neutral:
        // 中性就是平的一字嘴：不笑，也不撇
        default:                 apply_lip(0); break;
    }

    // 关键：每种情绪都要重算一次尺寸，否则上一个表情的张口状态会残留
    setWeight(getWeight());
}

void DefaultMouth::setWeight(int weight)
{
    Feature::setWeight(weight);

    // 记下最近一次"张口"的时刻：SpeakingModifier 会在 0~60 之间抖动，
    // 400ms 内张过口就算还在说话（判断见 _update）。
    if (_weight > 8) {
        _last_open_tick = lv_tick_get();
    }

    _apply();
}

void DefaultMouth::_update()
{
    // 只有带 _static_open 的表情（laugh / sleepy / surprised）需要这个判断
    if (!_static_open || !getVisible()) {
        return;
    }

    const uint32_t now  = lv_tick_get();
    const bool speaking = (_last_open_tick != 0) && (now - _last_open_tick < 400);

    if (speaking != _speaking) {
        _speaking = speaking;
        _apply();
    }
}

void DefaultMouth::_apply()
{
    int size_x = map_range(_weight, 0, 100, _mouth_min_size.x, _mouth_max_size.x);
    int size_y = map_range(_weight, 0, 100, _mouth_min_size.y, _mouth_max_size.y);
    int radius = map_range(_weight, 0, 100, _mouth_min_radius, _mouth_max_radius);

    // 说话期间不收口的话，laugh 会在停顿帧突然鼓成一个固定大小的方块，
    // 而 happy 在同样的帧里收成笑线 —— 同一句话里两种表现，看着就是"嘴不一致"。
    const bool static_open_now = _static_open && !_speaking;

    if (static_open_now && _weight <= 8) {
        size_x = _open_w;
        size_y = _open_h;
        radius = _open_h / 2;
    }

    // D 形嘴：开心/难过张嘴时，用直角矩形盖掉胶囊的一半，
    // 得到"平顶圆底"（笑）或"平底圆顶"（难过）。中性/疑惑/惊讶仍用普通圆角矩形。
    const bool d_shape = (_d_dir != 0) && (_weight > 8);
    if (d_shape) {
        radius = size_y / 2;
    }

    _mouth->setSize(size_x, size_y);
    _mouth->setRadius(radius);

    if (d_shape) {
        const int half_h = (size_y + 1) / 2;
        // 宽度必须和口腔完全一致，多出来的部分会从圆角外侧露出去
        lv_obj_set_size(_square_half, size_x, half_h);
        lv_obj_align(_square_half, _d_dir > 0 ? LV_ALIGN_TOP_MID : LV_ALIGN_BOTTOM_MID, 0, 0);
        lv_obj_remove_flag(_square_half, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(_square_half, LV_OBJ_FLAG_HIDDEN);
    }

    _update_visibility();
}

void DefaultMouth::setRotation(int rotation)
{
    Element::setRotation(rotation);

    _mouth->setTransformPivot(_mouth->getWidth() / 2, _mouth->getHeight() / 2);
    // LVGL 的 transform_rotation 本来就是 0.1 度单位，不要再乘 10，
    // 否则 imu.h 里 ±25（本意 ±2.5°）会变成 ±25°，张嘴的方块整个歪掉
    _mouth->setRotation(rotation);
}

void DefaultMouth::setVisible(bool visible)
{
    Element::setVisible(visible);

    _mouth->setHidden(!visible);
    if (!visible) {
        lv_obj_add_flag(_lip, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(_flat, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(_teeth, LV_OBJ_FLAG_HIDDEN);
        for (auto* g : _tooth_gap) {
            lv_obj_add_flag(g, LV_OBJ_FLAG_HIDDEN);
        }
    } else {
        setWeight(_weight);
    }
}
