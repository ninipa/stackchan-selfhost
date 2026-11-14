/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once
#include "../avatar/decorator.h"
#include <lvgl.h>
#include <smooth_lvgl.hpp>
#include <cstdint>
#include <memory>
#include <vector>

namespace stackchan::avatar {

/**
 * @brief
 *
 */
class HeartDecorator : public Decorator {
public:
    /**
     * @brief
     *
     * @param parent
     * @param destroyAfterMs Destroy after milliseconds, 0 for infinite
     * @param animationIntervalMs Animation update interval in milliseconds, 0 for none
     */
    HeartDecorator(lv_obj_t* parent, uint32_t destroyAfterMs = 0, uint32_t animationIntervalMs = 500);
    ~HeartDecorator();

    void _update() override;

    using Element::setPosition;

    void setPosition(int x, int y);
    void setRotation(int rotation) override;
    void setColor(lv_color_t color);

private:
    std::unique_ptr<uitk::lvgl_cpp::Image> _heart;

    uint32_t _destroy_at            = 0;
    uint32_t _next_animation_tick   = 0;
    uint32_t _animation_interval_ms = 0;
    bool _has_lifetime              = false;

    int _animation_index = 0;
};

/**
 * @brief
 *
 */
class AngryDecorator : public Decorator {
public:
    /**
     * @brief
     *
     * @param parent
     * @param destroyAfterMs Destroy after milliseconds, 0 for infinite
     * @param animationIntervalMs Animation update interval in milliseconds, 0 for none
     */
    AngryDecorator(lv_obj_t* parent, uint32_t destroyAfterMs = 0, uint32_t animationIntervalMs = 500);
    ~AngryDecorator();

    void _update() override;

    using Element::setPosition;

    void setPosition(int x, int y);
    void setRotation(int rotation) override;
    void setColor(lv_color_t color);

private:
    std::unique_ptr<uitk::lvgl_cpp::Image> _angry;

    uint32_t _destroy_at            = 0;
    uint32_t _next_animation_tick   = 0;
    uint32_t _animation_interval_ms = 0;
    bool _has_lifetime              = false;

    int _animation_index = 0;
};

/**
 * @brief
 *
 */
class SweatDecorator : public Decorator {
public:
    /**
     * @brief
     *
     * @param parent
     * @param destroyAfterMs Destroy after milliseconds, 0 for infinite
     * @param animationIntervalMs Animation update interval in milliseconds, 0 for none
     */
    SweatDecorator(lv_obj_t* parent, uint32_t destroyAfterMs = 0, uint32_t animationIntervalMs = 700);
    ~SweatDecorator();

    void _update() override;

    using Element::setPosition;

    void setPosition(int x, int y);
    void setRotation(int rotation) override;
    void setColor(lv_color_t color);
    void setVisible(bool visible) override;

private:
    std::unique_ptr<uitk::lvgl_cpp::Image> _sweat;

    uint32_t _destroy_at            = 0;
    uint32_t _next_animation_tick   = 0;
    uint32_t _animation_interval_ms = 0;
    bool _has_lifetime              = false;

    int _animation_index = 0;
};

/**
 * @brief
 *
 */
class ShyDecorator : public Decorator {
public:
    /**
     * @brief
     *
     * @param parent
     * @param destroyAfterMs Destroy after milliseconds, 0 for infinite
     */
    ShyDecorator(lv_obj_t* parent, uint32_t destroyAfterMs = 0);
    ~ShyDecorator();

    void _update() override;

    using Element::setPosition;

    void setPosition(int x, int y);
    void setRotation(int rotation) override;
    void setColor(lv_color_t color);
    void setVisible(bool visible) override;

private:
    std::unique_ptr<uitk::lvgl_cpp::Image> _left;
    std::unique_ptr<uitk::lvgl_cpp::Image> _right;

    uint32_t _destroy_at = 0;
    bool _has_lifetime   = false;
};

/**
 * @brief
 *
 */
class DizzyDecorator : public Decorator {
public:
    /**
     * @brief
     *
     * @param parent
     * @param destroyAfterMs Destroy after milliseconds, 0 for infinite
     * @param animationIntervalMs Animation update interval in milliseconds, 0 for none
     */
    DizzyDecorator(lv_obj_t* parent, uint32_t destroyAfterMs = 0, uint32_t animationIntervalMs = 500);
    ~DizzyDecorator();

    void _update() override;

    using Element::setPosition;

    void setPosition(int x, int y);
    void setRotation(int rotation) override;
    void setColor(lv_color_t color);
    void setVisible(bool visible) override;

private:
    std::unique_ptr<uitk::lvgl_cpp::Image> _left;
    std::unique_ptr<uitk::lvgl_cpp::Image> _right;

    uint32_t _destroy_at            = 0;
    uint32_t _next_animation_tick   = 0;
    uint32_t _animation_interval_ms = 0;
    bool _has_lifetime              = false;

    int _animation_index = 0;
};

/**
 * @brief 漫画式情绪符号（问号 / 感叹号）
 *
 * 贴图由调用方传入（decorator_question / decorator_exclam），统一走 recolor 染色，
 * 所以同一张蒙版可以换色复用。
 */
class SymbolDecorator : public Decorator {
public:
    /**
     * @param parent
     * @param src 图像描述符，需为 RGB565A8（A8 蒙版 + 白色 RGB）
     * @param destroyAfterMs Destroy after milliseconds, 0 for infinite
     * @param animationIntervalMs 上下浮动周期，0 为不浮动
     */
    SymbolDecorator(lv_obj_t* parent, const lv_image_dsc_t* src, uint32_t destroyAfterMs = 0,
                    uint32_t animationIntervalMs = 420);
    ~SymbolDecorator();

    void _update() override;

    using Element::setPosition;

    void setPosition(int x, int y);
    void setRotation(int rotation) override;
    void setColor(lv_color_t color);
    void setVisible(bool visible) override;

private:
    std::unique_ptr<uitk::lvgl_cpp::Image> _symbol;
    int _base_x = 0;
    int _base_y = 0;

    uint32_t _destroy_at            = 0;
    uint32_t _next_animation_tick   = 0;
    uint32_t _animation_interval_ms = 0;
    bool _has_lifetime              = false;

    // 出现时先弹一下：放大一帧再回到原尺寸，符号才有"跳出来"的感觉
    uint32_t _pop_until_tick = 0;

    int _animation_index = 0;
};

/**
 * @brief 眼角的一滴眼泪（难过）
 *
 * 从眼角往下滑，滑到底再从头开始，看起来像一直在哭。
 */
class TearDecorator : public Decorator {
public:
    TearDecorator(lv_obj_t* parent, uint32_t destroyAfterMs = 0, uint32_t animationIntervalMs = 150);
    ~TearDecorator();

    void _update() override;

    using Element::setPosition;

    void setPosition(int x, int y);
    void setRotation(int rotation) override;
    void setColor(lv_color_t color);
    void setVisible(bool visible) override;

private:
    std::unique_ptr<uitk::lvgl_cpp::Image> _tear;
    int _base_x = 0;
    int _base_y = 0;

    uint32_t _destroy_at            = 0;
    uint32_t _next_animation_tick   = 0;
    uint32_t _animation_interval_ms = 0;
    bool _has_lifetime              = false;

    int _animation_index = 0;
};

/**
 * @brief 三个飘着的 Z（困）
 *
 * 同一个素材放三份，靠缩放拉开层次，各自相位错开地上下浮动。
 */
class ZzzDecorator : public Decorator {
public:
    ZzzDecorator(lv_obj_t* parent, uint32_t destroyAfterMs = 0, uint32_t animationIntervalMs = 380);
    ~ZzzDecorator();

    void _update() override;

    using Element::setPosition;

    void setPosition(int x, int y);
    void setRotation(int rotation) override;
    void setColor(lv_color_t color);
    void setVisible(bool visible) override;

private:
    struct Letter {
        std::unique_ptr<uitk::lvgl_cpp::Image> img;
        int dx = 0;
        int dy = 0;
        int phase = 0;
    };
    std::vector<Letter> _letters;
    int _base_x = 0;
    int _base_y = 0;

    uint32_t _destroy_at            = 0;
    uint32_t _next_animation_tick   = 0;
    uint32_t _animation_interval_ms = 0;
    bool _has_lifetime              = false;

    int _animation_index = 0;
};

}  // namespace stackchan::avatar
