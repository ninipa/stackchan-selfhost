/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once
#include "../../avatar/avatar.h"
#include "../../avatar/elements/feature.h"
#include <lvgl.h>
#include <smooth_lvgl.hpp>
#include <memory>

namespace stackchan::avatar {

class DefaultBrow;

/**
 * @brief
 *
 */
class DefaultAvatar : public Avatar {
public:
    lv_color_t primaryColor   = lv_color_white();
    lv_color_t secondaryColor = lv_color_black();

    ~DefaultAvatar();

    void init(lv_obj_t* parent, const lv_font_t* font = &lv_font_montserrat_16);
    uitk::lvgl_cpp::Container* getPanel() const;
    void setEmotion(const Emotion& emotion) override;

private:
    std::unique_ptr<uitk::lvgl_cpp::Container> _pannel;
    std::unique_ptr<DefaultBrow> _left_brow;
    std::unique_ptr<DefaultBrow> _right_brow;
};

/**
 * @brief
 *
 */
class DefaultEyes : public Feature {
public:
    DefaultEyes(lv_obj_t* parent, lv_color_t primaryColor, lv_color_t secondaryColor, bool isLeftEye);
    ~DefaultEyes();

    void setPosition(const uitk::Vector2i& position) override;
    void setWeight(int weight) override;
    void setRotation(int rotation) override;
    void setEmotion(const Emotion& emotion) override;
    void setVisible(bool visible) override;
    void setSize(int size) override;
    void _update() override;

private:
    void apply_mode(bool open);

    bool _is_left_eye    = false;
    int _eyelid_offset_y = 0;
    lv_color_t _primary;
    lv_color_t _secondary;

    std::unique_ptr<uitk::lvgl_cpp::Container> _container;
    std::unique_ptr<uitk::lvgl_cpp::Container> _eye;
    std::unique_ptr<uitk::lvgl_cpp::Container> _eyelid;
    std::unique_ptr<uitk::lvgl_cpp::Container> _pupil;
    std::unique_ptr<uitk::lvgl_cpp::Container> _highlight;
    std::unique_ptr<uitk::lvgl_cpp::Container> _closed_line;
    std::unique_ptr<uitk::lvgl_cpp::Image> _spiral;
    lv_obj_t* _arc = nullptr;

    int _gaze_x = 0;   // 瞳孔偏移（疑惑/思考时向上看）
    int _gaze_y = 0;
    int _mode = 0;  // 0=正常睁眼 1=弧线眼 2=螺旋眼
    uint32_t _spin_tick = 0;
    int _spin_angle = 0;
};

/**
 * @brief 眉毛：一条可旋转的胶囊
 *
 */
class DefaultBrow : public Feature {
public:
    DefaultBrow(lv_obj_t* parent, lv_color_t color, bool isLeft);
    ~DefaultBrow();

    void setEmotion(const Emotion& emotion) override;

private:
    bool _is_left = false;
    std::unique_ptr<uitk::lvgl_cpp::Container> _bar;
};

/**
 * @brief
 *
 */
class DefaultMouth : public Feature {
public:
    DefaultMouth(lv_obj_t* parent, lv_color_t primaryColor, lv_color_t secondaryColor);
    ~DefaultMouth();

    void setPosition(const uitk::Vector2i& position) override;
    void setWeight(int weight) override;
    void setRotation(int rotation) override;
    void setVisible(bool visible) override;
    void setEmotion(const Emotion& emotion) override;

private:
    void _update() override;
    void _apply();
    void apply_lip(int inner);
    void _place_teeth();
    void _update_visibility();

    std::unique_ptr<uitk::lvgl_cpp::Container> _mouth;
    lv_obj_t* _lip = nullptr;
    lv_obj_t* _flat = nullptr;
    lv_obj_t* _teeth = nullptr;      // 龇牙：白色底板
    lv_obj_t* _tooth_gap[3] = {nullptr, nullptr, nullptr};  // 牙缝
    lv_obj_t* _square_half = nullptr;  // D 形嘴：盖住胶囊的一半，做出"平顶圆底/平底圆顶"
    bool _teeth_on = false;
    int _lip_angle = 0;
    // 0 = 不用 D 形；+1 = 平顶圆底（开心）；-1 = 平底圆顶（难过）
    int _d_dir = 0;
    bool _static_open = false;
    int _open_w = 0;
    int _open_h = 0;
    // 说话期间（最近 400ms 内张过口）暂时忽略 _static_open：
    // laugh/sleepy/surprised 的"常开嘴"是静息态的样子，说话时应和其他表情一样跟着权重收口。
    uint32_t _last_open_tick = 0;
    bool _speaking = false;
};

/**
 * @brief
 *
 */
class DefaultSpeechBubble : public SpeechBubble {
public:
    DefaultSpeechBubble(lv_obj_t* parent, lv_color_t primaryColor, lv_color_t secondaryColor, const lv_font_t* font);
    ~DefaultSpeechBubble();

    void setSpeech(std::string_view text) override;
    void clearSpeech() override;
    void setVisible(bool visible) override;
    void setTextFont(void* font) override;

private:
    std::unique_ptr<uitk::lvgl_cpp::Container> _container;
    std::unique_ptr<uitk::lvgl_cpp::Image> _arrow;
    std::unique_ptr<uitk::lvgl_cpp::Container> _bubble;
    std::unique_ptr<uitk::lvgl_cpp::Label> _text;
};

}  // namespace stackchan::avatar
