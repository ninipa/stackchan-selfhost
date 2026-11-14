/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "default.h"

using namespace uitk::lvgl_cpp;
using namespace stackchan::avatar;

void DefaultAvatar::init(lv_obj_t* parent, const lv_font_t* font)
{
    _pannel = std::make_unique<Container>(parent);
    _pannel->align(LV_ALIGN_CENTER, 0, 0);
    _pannel->setSize(320, 240);
    _pannel->setRadius(0);
    _pannel->setBorderWidth(0);
    _pannel->setBgColor(secondaryColor);
    _pannel->removeFlag(LV_OBJ_FLAG_SCROLLABLE);

    _key_elements.leftEye  = std::make_unique<DefaultEyes>(_pannel->get(), primaryColor, secondaryColor, true);
    _key_elements.rightEye = std::make_unique<DefaultEyes>(_pannel->get(), primaryColor, secondaryColor, false);
    _key_elements.mouth    = std::make_unique<DefaultMouth>(_pannel->get(), primaryColor, secondaryColor);
    _left_brow  = std::make_unique<DefaultBrow>(_pannel->get(), primaryColor, true);
    _right_brow = std::make_unique<DefaultBrow>(_pannel->get(), primaryColor, false);

    _key_elements.speechBubble =
        std::make_unique<DefaultSpeechBubble>(_pannel->get(), primaryColor, secondaryColor, font);
}

DefaultAvatar::~DefaultAvatar()
{
    // 顺序很重要：先销毁面板里的各个元素，最后再删面板本身。
    // 反过来的话（先 _pannel.reset()），LVGL 会把面板连同子对象一起删掉，
    // 之后 DefaultEyes / DefaultMouth 析构时还拿着已经失效的 lv_obj_t* 去做
    // lv_obj_is_valid / lv_obj_delete —— 指针恰好被复用时会误判为“还有效”，
    // 于是二次释放，堆被写坏（lvsim 里表现为下一个 new 直接崩）。
    _key_elements.speechBubble.reset();
    _key_elements.mouth.reset();
    _key_elements.rightEye.reset();
    _key_elements.leftEye.reset();
    _right_brow.reset();
    _left_brow.reset();
    _pannel.reset();
}

void DefaultAvatar::setEmotion(const Emotion& emotion)
{
    Avatar::setEmotion(emotion);

    if (_left_brow) {
        _left_brow->setEmotion(emotion);
    }
    if (_right_brow) {
        _right_brow->setEmotion(emotion);
    }
}

Container* DefaultAvatar::getPanel() const
{
    if (_pannel) {
        return _pannel.get();
    }
    return NULL;
}


// ------------------------------- 眉毛 --------------------------------------

static const uitk::Vector2i _brow_pos = uitk::Vector2i(-52, -54);
static const int _brow_w               = 34;
static const int _brow_h               = 9;

DefaultBrow::DefaultBrow(lv_obj_t* parent, lv_color_t color, bool isLeft)
{
    _is_left = isLeft;

    _bar = std::make_unique<Container>(parent);
    _bar->setSize(_brow_w, _brow_h);
    _bar->setRadius(_brow_h / 2);
    _bar->setBorderWidth(0);
    _bar->setBgColor(color);
    _bar->removeFlag(LV_OBJ_FLAG_SCROLLABLE);
    _bar->setTransformPivot(_brow_w / 2, _brow_h / 2);
    // 必须用 align 相对中心定位，直接 setPos 会按左上角算，跑到屏幕外
    _bar->align(LV_ALIGN_CENTER, _is_left ? _brow_pos.x : -_brow_pos.x, _brow_pos.y);
}

DefaultBrow::~DefaultBrow()
{
    _bar.reset();
}

void DefaultBrow::setEmotion(const Emotion& emotion)
{
    int angle = 0;
    int dy    = 0;

    // 高兴/大笑用平眉：弯眼本身已经在笑，眉毛再斜会读成"得意"或"担心"
    // 想回到斜眉，编译时加 -DBROW_ANGLED_HAPPY
#ifdef BROW_ANGLED_HAPPY
    constexpr int happy_ang = -22;
    constexpr int laugh_ang = -32;
#else
    constexpr int happy_ang = 0;
    constexpr int laugh_ang = 0;
#endif

    switch (emotion) {
        case Emotion::Happy:     angle = happy_ang; break;
        case Emotion::Laugh:     angle = laugh_ang; break;
        case Emotion::Angry:     angle = 26;  dy = 5; break;   // 向下压
        case Emotion::Sad:       angle = -30; break;
        case Emotion::Doubt:     angle = -14; dy = -5; break;
        case Emotion::Sleepy:    angle = 6;   dy = 4; break;
        case Emotion::Surprised: angle = 0;   dy = -10; break;  // 抬高且放平
        case Emotion::Dizzy:     angle = 4; break;
        case Emotion::Neutral:
        default:                 angle = 0; break;
    }

    if (emotion == Emotion::Doubt) {
        // 一高一低才是疑惑；对称的话会读成难过
        _bar->setRotation((_is_left ? -32 : -6) * 10);
    } else {
        _bar->setRotation((_is_left ? angle : -angle) * 10);  // 0.1 度单位
    }

    _bar->align(LV_ALIGN_CENTER, _is_left ? _brow_pos.x : -_brow_pos.x, _brow_pos.y + dy);
}
