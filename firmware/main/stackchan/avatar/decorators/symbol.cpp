/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 * SPDX-License-Identifier: MIT
 */
#include "decorators.h"
#include <hal/hal.h>
#include <vector>

using namespace uitk;
using namespace uitk::lvgl_cpp;
using namespace stackchan::avatar;

LV_IMAGE_DECLARE(decorator_question);
LV_IMAGE_DECLARE(decorator_exclam);
LV_IMAGE_DECLARE(decorator_z);
LV_IMAGE_DECLARE(decorator_tear);

// 右侧偏上，和 AngryDecorator 同一带，但更靠外一点，避免压住眉毛
static const Vector2i _symbol_default_position = Vector2i(116, -74);
static const lv_color_t _symbol_default_color  = lv_color_hex(0xFFFFFF);
static const std::vector<int> _symbol_bob_frames = {0, -5, -8, -5};

SymbolDecorator::SymbolDecorator(lv_obj_t* parent, const lv_image_dsc_t* src, uint32_t destroyAfterMs,
                                 uint32_t animationIntervalMs)
    : _animation_interval_ms(animationIntervalMs)
{
    _base_x = _symbol_default_position.x;
    _base_y = _symbol_default_position.y;

    _symbol = std::make_unique<Image>(parent);
    _symbol->setSrc(src);
    _symbol->setAlign(LV_ALIGN_CENTER);
    _symbol->setPos(_base_x, _base_y);

    _symbol->setTransformPivot(_symbol->getWidth() / 2, _symbol->getHeight() / 2);
    _symbol->setImageRecolorOpa(LV_OPA_COVER);
    _symbol->setImageRecolor(_symbol_default_color);

    uint32_t now = GetHAL().millis();

    if (destroyAfterMs > 0) {
        _destroy_at   = now + destroyAfterMs;
        _has_lifetime = true;
    }

    if (_animation_interval_ms > 0) {
        _next_animation_tick = now + _animation_interval_ms;
    }

    // 出现时的"弹出"：260ms 内先放大到 1.25 倍
    _pop_until_tick = now + 260;
    _symbol->setScale(320);
}

SymbolDecorator::~SymbolDecorator()
{
}

void SymbolDecorator::_update()
{
    uint32_t now = GetHAL().millis();

    if (_has_lifetime && now >= _destroy_at) {
        requestDestroy();
        return;
    }

    if (_pop_until_tick != 0) {
        if (now < _pop_until_tick) {
            return;  // 弹出期间不做浮动，免得两个动画打架
        }
        _pop_until_tick = 0;
        _symbol->setScale(256);
    }

    if (_animation_interval_ms > 0 && now >= _next_animation_tick) {
        _next_animation_tick = now + _animation_interval_ms;
        _animation_index     = (_animation_index + 1) % _symbol_bob_frames.size();
        _symbol->setPos(_base_x, _base_y + _symbol_bob_frames[_animation_index]);
    }
}

void SymbolDecorator::setPosition(int x, int y)
{
    _base_x = x;
    _base_y = y;
    if (_symbol) {
        _symbol->setPos(x, y);
    }
}

void SymbolDecorator::setRotation(int rotation)
{
    if (_symbol) {
        _symbol->setRotation(rotation);
    }
}

void SymbolDecorator::setColor(lv_color_t color)
{
    if (_symbol) {
        _symbol->setImageRecolor(color);
    }
}

void SymbolDecorator::setVisible(bool visible)
{
    Element::setVisible(visible);
    if (_symbol) {
        _symbol->setHidden(!visible);
    }
}

// ---------------------------------------------------------------- 眼泪

static const Vector2i _tear_default_position = Vector2i(-66, 7);
static const lv_color_t _tear_default_color  = lv_color_hex(0x8FD8FF);
// 沿脸颊往下滑，最后一步回到起点，形成循环
static const std::vector<int> _tear_dy_frames = {0, 3, 7, 11, 15, 19};

TearDecorator::TearDecorator(lv_obj_t* parent, uint32_t destroyAfterMs, uint32_t animationIntervalMs)
    : _animation_interval_ms(animationIntervalMs)
{
    _base_x = _tear_default_position.x;
    _base_y = _tear_default_position.y;

    _tear = std::make_unique<Image>(parent);
    _tear->setSrc(&decorator_tear);
    _tear->setAlign(LV_ALIGN_CENTER);
    _tear->setPos(_base_x, _base_y);

    _tear->setTransformPivot(_tear->getWidth() / 2, _tear->getHeight() / 2);
    _tear->setImageRecolorOpa(LV_OPA_COVER);
    _tear->setImageRecolor(_tear_default_color);

    uint32_t now = GetHAL().millis();

    if (destroyAfterMs > 0) {
        _destroy_at   = now + destroyAfterMs;
        _has_lifetime = true;
    }
    if (_animation_interval_ms > 0) {
        _next_animation_tick = now + _animation_interval_ms;
    }
}

TearDecorator::~TearDecorator()
{
}

void TearDecorator::_update()
{
    uint32_t now = GetHAL().millis();

    if (_has_lifetime && now >= _destroy_at) {
        requestDestroy();
        return;
    }

    if (_animation_interval_ms > 0 && now >= _next_animation_tick) {
        _next_animation_tick = now + _animation_interval_ms;
        _animation_index     = (_animation_index + 1) % _tear_dy_frames.size();
        _tear->setPos(_base_x, _base_y + _tear_dy_frames[_animation_index]);
    }
}

void TearDecorator::setPosition(int x, int y)
{
    _base_x = x;
    _base_y = y;
    if (_tear) {
        _tear->setPos(x, y);
    }
}

void TearDecorator::setRotation(int rotation)
{
    if (_tear) {
        _tear->setRotation(rotation);
    }
}

void TearDecorator::setColor(lv_color_t color)
{
    if (_tear) {
        _tear->setImageRecolor(color);
    }
}

void TearDecorator::setVisible(bool visible)
{
    Element::setVisible(visible);
    if (_tear) {
        _tear->setHidden(!visible);
    }
}

// ---------------------------------------------------------------- Zzz

static const Vector2i _zzz_default_position = Vector2i(84, -72);
static const lv_color_t _zzz_default_color  = lv_color_hex(0xFFFFFF);
// 每个 Z 的偏移与缩放（256 = 原尺寸），越靠上越小，做出往远处飘的感觉
static const int _zzz_layout[3][3] = {
    {0, 0, 150},    // dx, dy, scale
    {22, -14, 200},
    {40, -26, 256},
};
static const std::vector<int> _zzz_bob_frames = {0, -3, -5, -2};

ZzzDecorator::ZzzDecorator(lv_obj_t* parent, uint32_t destroyAfterMs, uint32_t animationIntervalMs)
    : _animation_interval_ms(animationIntervalMs)
{
    _base_x = _zzz_default_position.x;
    _base_y = _zzz_default_position.y;

    for (int i = 0; i < 3; i++) {
        Letter letter;
        letter.dx    = _zzz_layout[i][0];
        letter.dy    = _zzz_layout[i][1];
        letter.phase = i;

        letter.img = std::make_unique<Image>(parent);
        letter.img->setSrc(&decorator_z);
        letter.img->setAlign(LV_ALIGN_CENTER);
        letter.img->setPos(_base_x + letter.dx, _base_y + letter.dy);
        letter.img->setTransformPivot(letter.img->getWidth() / 2, letter.img->getHeight() / 2);
        letter.img->setImageRecolorOpa(LV_OPA_COVER);
        letter.img->setImageRecolor(_zzz_default_color);
        letter.img->setScale(_zzz_layout[i][2]);

        _letters.push_back(std::move(letter));
    }

    uint32_t now = GetHAL().millis();

    if (destroyAfterMs > 0) {
        _destroy_at   = now + destroyAfterMs;
        _has_lifetime = true;
    }
    if (_animation_interval_ms > 0) {
        _next_animation_tick = now + _animation_interval_ms;
    }
}

ZzzDecorator::~ZzzDecorator()
{
}

void ZzzDecorator::_update()
{
    uint32_t now = GetHAL().millis();

    if (_has_lifetime && now >= _destroy_at) {
        requestDestroy();
        return;
    }

    if (_animation_interval_ms > 0 && now >= _next_animation_tick) {
        _next_animation_tick = now + _animation_interval_ms;
        _animation_index     = (_animation_index + 1) % _zzz_bob_frames.size();

        for (auto& letter : _letters) {
            // 三个 Z 相位错开，看起来是一串往上飘
            const int frame = (_animation_index + letter.phase) % _zzz_bob_frames.size();
            letter.img->setPos(_base_x + letter.dx, _base_y + letter.dy + _zzz_bob_frames[frame]);
        }
    }
}

void ZzzDecorator::setPosition(int x, int y)
{
    _base_x = x;
    _base_y = y;
    for (auto& letter : _letters) {
        letter.img->setPos(x + letter.dx, y + letter.dy);
    }
}

void ZzzDecorator::setRotation(int rotation)
{
    for (auto& letter : _letters) {
        letter.img->setRotation(rotation);
    }
}

void ZzzDecorator::setColor(lv_color_t color)
{
    for (auto& letter : _letters) {
        letter.img->setImageRecolor(color);
    }
}

void ZzzDecorator::setVisible(bool visible)
{
    Element::setVisible(visible);
    for (auto& letter : _letters) {
        letter.img->setHidden(!visible);
    }
}
