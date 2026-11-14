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

LV_IMAGE_DECLARE(decorator_dizzy);

static const Vector2i _eye_pos        = Vector2i(-52, -14);
static const Vector2i _eye_min_offset = Vector2i(-16, -16);
static const Vector2i _eye_max_offset = Vector2i(16, 16);
static const Vector2i _eye_size_limit = Vector2i(24, 64);

// 瞳孔 / 高光比例（原型里的 B 方案）
static constexpr int PUPIL_PCT = 62;
static constexpr int HL_PCT    = 45;

DefaultEyes::DefaultEyes(lv_obj_t* parent, lv_color_t primaryColor, lv_color_t secondaryColor, bool isLeftEye)
{
    _is_left_eye = isLeftEye;
    _primary     = primaryColor;
    _secondary   = secondaryColor;

    _container = std::make_unique<Container>(parent);
    _container->setRadius(0);
    _container->setAlign(LV_ALIGN_CENTER);
    _container->setBorderWidth(0);
    _container->setBgOpa(0);
    _container->removeFlag(LV_OBJ_FLAG_SCROLLABLE);
    _container->setPadding(0, 0, 0, 0);
    _container->setTransformPivot(40, 40);
    _container->setSize(80, 80);

    _eye = std::make_unique<Container>(_container->get());
    _eye->setRadius(LV_RADIUS_CIRCLE);
    _eye->align(LV_ALIGN_CENTER, 0, 0);
    _eye->setBorderWidth(0);
    _eye->setBgColor(primaryColor);
    _eye->removeFlag(LV_OBJ_FLAG_SCROLLABLE);

    _pupil = std::make_unique<Container>(_eye->get());
    _pupil->setRadius(LV_RADIUS_CIRCLE);
    _pupil->setBorderWidth(0);
    _pupil->setBgColor(secondaryColor);
    _pupil->removeFlag(LV_OBJ_FLAG_SCROLLABLE);
    _pupil->align(LV_ALIGN_CENTER, 0, 0);

    _highlight = std::make_unique<Container>(_eye->get());
    _highlight->setRadius(LV_RADIUS_CIRCLE);
    _highlight->setBorderWidth(0);
    _highlight->setBgColor(primaryColor);
    _highlight->removeFlag(LV_OBJ_FLAG_SCROLLABLE);

    // 闭眼时保留的一条细线，避免"眼睛整个消失"
    _closed_line = std::make_unique<Container>(_container->get());
    _closed_line->setRadius(LV_RADIUS_CIRCLE);
    _closed_line->setBorderWidth(0);
    _closed_line->setBgColor(primaryColor);
    _closed_line->align(LV_ALIGN_CENTER, 0, 0);
    _closed_line->addFlag(LV_OBJ_FLAG_HIDDEN);

    // 弯眼 ∩：用 lv_arc 画一段圆弧（康角范围决定深浅）
    _arc = lv_arc_create(_container->get());
    lv_obj_remove_style_all(_arc);
    lv_obj_remove_flag(_arc, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_arc_opa(_arc, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_arc_opa(_arc, LV_OPA_TRANSP, LV_PART_KNOB);
    lv_obj_set_style_bg_opa(_arc, LV_OPA_TRANSP, LV_PART_KNOB);
    lv_obj_set_style_arc_color(_arc, primaryColor, LV_PART_INDICATOR);
    lv_arc_set_bg_angles(_arc, 0, 360);
    lv_arc_set_angles(_arc, 205, 335);
    lv_obj_align(_arc, LV_ALIGN_CENTER, 0, 0);
    lv_obj_add_flag(_arc, LV_OBJ_FLAG_HIDDEN);

    // 眩晕用的螺旋（复用装饰器素材）
    _spiral = std::make_unique<Image>(_container->get());
    _spiral->setSrc(&decorator_dizzy);
    _spiral->align(LV_ALIGN_CENTER, 0, 0);
    _spiral->addFlag(LV_OBJ_FLAG_HIDDEN);

    _eyelid = std::make_unique<Container>(_container->get());
    _eyelid->setRadius(LV_RADIUS_CIRCLE);
    _eyelid->align(LV_ALIGN_CENTER, 0, 0);
    _eyelid->setBorderWidth(0);
    _eyelid->setBgColor(secondaryColor);
    _eyelid->removeFlag(LV_OBJ_FLAG_SCROLLABLE);

    setSize(0);
    setWeight(100);
    setPosition(_position);
    setRotation(0);
}

DefaultEyes::~DefaultEyes()
{
    _spiral.reset();
    if (_arc != nullptr && lv_obj_is_valid(_arc)) {
        lv_obj_delete(_arc);
    }
    _arc = nullptr;
    _highlight.reset();
    _pupil.reset();
    _closed_line.reset();
    _eyelid.reset();
    _eye.reset();
    _container.reset();
}

void DefaultEyes::setPosition(const Vector2i& position)
{
    Element::setPosition(position);

    auto pos_x = _is_left_eye ? _eye_pos.x : -_eye_pos.x;
    pos_x += map_range(_position.x, -100, 100, _eye_min_offset.x, _eye_max_offset.x);
    auto pos_y = _eye_pos.y + map_range(_position.y, -100, 100, _eye_min_offset.y, _eye_max_offset.y);

    _container->setPos(pos_x, pos_y);
    _eyelid->setY(_eyelid_offset_y);
}

void DefaultEyes::setWeight(int weight)
{
    Feature::setWeight(weight);

    if (_mode != 0) {
        return;  // 弧线眼 / 螺旋眼不参与眨眼
    }

    _eyelid_offset_y = -map_range(_weight, 0, 100, 0, (int)_eyelid->getHeight());
    _eyelid->setY(_eyelid_offset_y);

    bool closed = (_weight <= 6);
    _eye->setHidden(closed);
    _eyelid->setHidden(closed);
    _closed_line->setHidden(!closed);
}

void DefaultEyes::setRotation(int rotation)
{
    Element::setRotation(rotation);

    if (_mode != 2) {
        // LVGL 的 transform_rotation 就是 0.1 度单位，不要再乘 10
        _container->setRotation(rotation);
    }
}

void DefaultEyes::setEmotion(const Emotion& emotion)
{
    if (getIgnoreEmotion()) {
        return;
    }

    auto apply_style = [this](int weight, int rotation) {
        setWeight(weight);
        if (_is_left_eye) {
            setRotation(rotation);
        } else {
            setRotation(-rotation);
        }
    };

    _mode   = 0;
    _gaze_x = 0;
    _gaze_y = 0;

    switch (emotion) {
        case Emotion::Happy:
        case Emotion::Laugh:
            _mode = 1;
            break;
        case Emotion::Dizzy:
            _mode = 2;
            break;
        case Emotion::Angry:
            apply_style(56, 0);   // 眯起来才像在瞪人
            break;
        case Emotion::Sad:
            apply_style(84, 0);
            break;
        case Emotion::Doubt:
            apply_style(88, 0);
            _gaze_x = -12;        // 往上看、往侧看 = 在想事情
            _gaze_y = -15;
            apply_style(88, 0);
            break;
        case Emotion::Sleepy:
            apply_style(24, 0);   // 只留一条缝
            _gaze_y = 3;          // 视线下垂
            apply_style(24, 0);
            break;
        case Emotion::Surprised:
            apply_style(108, 0);  // 瞪到最大
            break;
        case Emotion::Neutral:
        default:
            apply_style(100, 0);
            break;
    }

    // 切换显示模式
    bool open = (_mode == 0);
    _eye->setHidden(!open);
    _eyelid->setHidden(!open || _weight <= 6);
    _closed_line->setHidden(!open || _weight > 6);

    if (_mode == 1) {
        lv_obj_remove_flag(_arc, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(_arc, LV_OBJ_FLAG_HIDDEN);
    }

    if (_mode == 2) {
        _spiral->removeFlag(LV_OBJ_FLAG_HIDDEN);
    } else {
        _spiral->addFlag(LV_OBJ_FLAG_HIDDEN);
    }
}

void DefaultEyes::_update()
{
    if (_mode != 2) {
        return;
    }

    uint32_t now = lv_tick_get();
    if (now - _spin_tick >= 100) {
        _spin_tick  = now;
        _spin_angle = (_spin_angle + 120) % 3600;  // 0.1° 单位 → 约 120°/秒
        _container->setRotation(_spin_angle);
    }
}

void DefaultEyes::setVisible(bool visible)
{
    Element::setVisible(visible);

    _container->setHidden(!visible);
}

void DefaultEyes::setSize(int size)
{
    Feature::setSize(size);

    int eye_size = map_range(_size, -100, 100, _eye_size_limit.x, _eye_size_limit.y);

    int eye_w = eye_size;
    int eye_h = (eye_size * 127) / 100;  // 竖椭圆，接近原型的 54x68
    _eye->setSize(eye_w, eye_h);
    _eyelid->setSize(eye_w, eye_h);
    _eye->setRadius(eye_w / 2);
    _eyelid->setRadius(eye_w / 5);  // 下缘接近平直：半闭才是"眼皮垂下来"，不是月牙

    int pupil_size = (eye_w * PUPIL_PCT) / 100;
    if (pupil_size < 2) {
        pupil_size = 2;
    }
    _pupil->setSize(pupil_size, pupil_size);
    _pupil->align(LV_ALIGN_CENTER, _gaze_x, _gaze_y);

    int hl_size = (pupil_size * HL_PCT) / 100;  // 高光占比
    if (hl_size < 1) {
        hl_size = 1;
    }
    _highlight->setSize(hl_size, hl_size);
    _highlight->align(LV_ALIGN_CENTER, _gaze_x - pupil_size / 5, _gaze_y - pupil_size / 5);

    _closed_line->setSize((eye_w * 78) / 100, 6);
    _closed_line->align(LV_ALIGN_CENTER, 0, 0);

    // 弯眼：用正方形画布，半径由尺寸决定，深浅由角度决定
    int arc_d = (eye_size * 150) / 100;
    lv_obj_set_size(_arc, arc_d, arc_d);
    lv_obj_set_style_arc_width(_arc, eye_size / 6 < 4 ? 4 : eye_size / 6, LV_PART_INDICATOR);
    lv_obj_align(_arc, LV_ALIGN_CENTER, 0, eye_size / 4);
    lv_arc_set_angles(_arc, 208, 332);

    _spiral->setSize((eye_size * 175) / 100, (eye_size * 175) / 100);

    setWeight(getWeight());
}
