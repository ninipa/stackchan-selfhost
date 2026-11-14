#pragma once
// lvsim 专用：xiaozhi 的主题系统在模拟器里不存在。
// 给一个最小实现让 countdown_overlay 编过——GetTheme() 全返回 nullptr，
// overlay 就会退回 BUILTIN_TEXT_FONT（模拟器只关心布局，不需要真机那套字体资产）。

#include <lvgl.h>

#include <memory>

#ifndef BUILTIN_TEXT_FONT
#define BUILTIN_TEXT_FONT lv_font_montserrat_20
#endif

class LvglFont {
public:
    virtual const lv_font_t* font() const = 0;
    virtual ~LvglFont() = default;
};

class LvglTheme {
public:
    virtual std::shared_ptr<LvglFont> GetTextFont() const = 0;
    virtual ~LvglTheme() = default;
};

class LvglThemeManager {
public:
    static LvglThemeManager& GetInstance()
    {
        static LvglThemeManager instance;
        return instance;
    }
    // 模拟器没有主题资产：一律取不到，让 overlay 用内置字体
    LvglTheme* GetTheme(const char*) const
    {
        return nullptr;
    }
};
