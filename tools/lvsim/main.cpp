// 桌面「行为」模拟器：跑真实的 avatar + modifier 代码，逐帧出图和状态。
//
// 和旧的九宫格渲染相比，多了两层东西：
//   1) 真的跑 modifier（眨眼 / 呼吸 / 待机微表情 / 摇晃 / 摸头 / 说话），
//      这样"表情 + 修饰器"叠加出来的 bug 能在桌面复现；
//   2) 每帧打印 LVGL 对象树和量化指标（眼睛区有几个 image、嘴巴倾斜多少），
//      不靠肉眼找问题。
//
// 硬件相关的东西用桩替代：HAL 只给 millis/delay 和两个事件信号，
// 舵机用 FakeServo（记角度、不驱动），霓虹灯用 FakeNeon（什么都不做）。
#include <lvgl.h>

#include <cstdio>
#include <cstring>
#include <ctime>
#include <memory>
#include <string>
#include <vector>

#include <hal/hal.h>  // 走 stub/

#include "stackchan/avatar/skins/default/default.h"
#include "stackchan/avatar/skins/default/countdown_overlay.h"
#include "stackchan/avatar/decorators/decorators.h"
#include "stackchan/modifiable.h"
#include "stackchan/modifiers/modifiers.h"
#include "stackchan/motion/motion.h"

LV_IMAGE_DECLARE(decorator_question);
LV_IMAGE_DECLARE(decorator_exclam);

static constexpr int W = 320;
static constexpr int H = 240;

static uint32_t g_ms = 0;

// ---------------------------------------------------------------- HAL 桩

Hal& GetHAL()
{
    static Hal hal;
    return hal;
}

uint32_t Hal::millis() const
{
    return g_ms;
}

void Hal::delay(uint32_t ms)
{
    g_ms += ms;
}

// ---------------------------------------------------------------- 假硬件

class FakeServo : public stackchan::motion::Servo {
public:
    void init() override
    {
    }
    void update() override
    {
    }
    void set_angle_impl(int angle) override
    {
        _angle = angle;
    }
    int getCurrentAngle() override
    {
        return _angle;
    }
    bool is_moving_impl() override
    {
        return false;
    }
    void rotate(int) override
    {
    }
    void setTorqueEnabled(bool) override
    {
    }
    bool getTorqueEnabled() override
    {
        return true;
    }

private:
    int _angle = 0;
};

class FakeNeon : public stackchan::addon::NeonLight {
public:
    FakeNeon() : NeonLight(0)
    {
    }

protected:
    void set_rgb_color_impl(uint8_t, uint8_t, uint8_t, uint8_t) override
    {
    }
    void refresh_rgb_impl() override
    {
    }
};

// ---------------------------------------------------------------- 宿主

class Harness : public stackchan::Modifiable {
public:
    void setup()
    {
        _motion = std::make_unique<stackchan::motion::Motion>(std::make_unique<FakeServo>(),
                                                             std::make_unique<FakeServo>());
        _motion->init();

        auto avatar = std::make_unique<stackchan::avatar::DefaultAvatar>();
        avatar->init(lv_screen_active());
        _avatar = std::move(avatar);
    }

    stackchan::motion::Motion& motion() override
    {
        return *_motion;
    }
    stackchan::avatar::Avatar& avatar() override
    {
        return *_avatar;
    }
    bool hasAvatar() override
    {
        return _avatar != nullptr;
    }
    stackchan::addon::NeonLight& leftNeonLight() override
    {
        return _neon;
    }
    stackchan::addon::NeonLight& rightNeonLight() override
    {
        return _neon;
    }

    void add(std::unique_ptr<stackchan::Modifier> modifier)
    {
        _modifiers.push_back(std::move(modifier));
    }

    // 推进一帧：先跑 modifier（和真机 StackChan::update 的顺序一致），再更新 avatar/motion
    void step(uint32_t dt_ms)
    {
        g_ms += dt_ms;
        for (auto& m : _modifiers) {
            m->_update(*this);
        }
        if (_avatar) {
            _avatar->update();
        }
        if (_motion) {
            _motion->update();
        }
    }

private:
    std::unique_ptr<stackchan::motion::Motion> _motion;
    std::unique_ptr<stackchan::avatar::Avatar> _avatar;
    std::vector<std::unique_ptr<stackchan::Modifier>> _modifiers;
    FakeNeon _neon;
};

// ---------------------------------------------------------------- 渲染

static uint16_t g_fb[W * H];

static void flush_cb(lv_display_t* disp, const lv_area_t* area, uint8_t* px)
{
    const int w = area->x2 - area->x1 + 1;
    for (int y = area->y1; y <= area->y2; y++) {
        memcpy(&g_fb[y * W + area->x1], px + (size_t)(y - area->y1) * w * 2, (size_t)w * 2);
    }
    lv_display_flush_ready(disp);
}

static void dump_ppm(const std::string& path)
{
    FILE* f = fopen(path.c_str(), "wb");
    if (!f) {
        printf("无法写入 %s\n", path.c_str());
        return;
    }
    fprintf(f, "P6\n%d %d\n255\n", W, H);
    for (int i = 0; i < W * H; i++) {
        uint16_t v = g_fb[i];
        unsigned char rgb[3] = {
            (unsigned char)(((v >> 11) & 0x1F) << 3),
            (unsigned char)(((v >> 5) & 0x3F) << 2),
            (unsigned char)((v & 0x1F) << 3),
        };
        fwrite(rgb, 1, 3, f);
    }
    fclose(f);
}

static void render(lv_display_t* disp)
{
    lv_obj_invalidate(lv_screen_active());
    lv_refr_now(disp);
}

// ---------------------------------------------------------------- 状态转储

static const char* _class_name(lv_obj_t* obj)
{
    if (lv_obj_check_type(obj, &lv_label_class)) return "label";
    if (lv_obj_check_type(obj, &lv_image_class)) return "image";
    if (lv_obj_check_type(obj, &lv_arc_class)) return "arc";
    if (lv_obj_check_type(obj, &lv_obj_class)) return "cont";
    return "?";
}

static void dump_tree_rec(lv_obj_t* obj, int depth)
{
    lv_area_t a;
    lv_obj_get_coords(obj, &a);
    const bool hidden = lv_obj_has_flag(obj, LV_OBJ_FLAG_HIDDEN);

    printf("%*s%-5s (%4d,%4d) %3dx%-3d%s\n", depth * 2, "", _class_name(obj), a.x1, a.y1, a.x2 - a.x1 + 1,
           a.y2 - a.y1 + 1, hidden ? "  HIDDEN" : "");

    const uint32_t n = lv_obj_get_child_count(obj);
    for (uint32_t i = 0; i < n; i++) {
        dump_tree_rec(lv_obj_get_child(obj, i), depth + 1);
    }
}

// 眼睛区里可见的 image 数量：>1 就是"同一侧多个眼部件"（比如螺旋 + 眩晕装饰器）
struct EyeStats {
    int left  = 0;
    int right = 0;
};

static void scan_images(lv_obj_t* obj, EyeStats& st)
{
    if (!lv_obj_has_flag(obj, LV_OBJ_FLAG_HIDDEN) && lv_obj_check_type(obj, &lv_image_class)) {
        lv_area_t a;
        lv_obj_get_coords(obj, &a);
        const int cx = (a.x1 + a.x2) / 2;
        const int cy = (a.y1 + a.y2) / 2;
        if (cy > 60 && cy < 130) {
            if (cx < 140) {
                st.left++;
            } else if (cx > 180) {
                st.right++;
            }
        }
    }

    const uint32_t n = lv_obj_get_child_count(obj);
    for (uint32_t i = 0; i < n; i++) {
        scan_images(lv_obj_get_child(obj, i), st);
    }
}

// 嘴巴歪不歪：取屏幕下半部的白色像素，比较左三分之一和右三分之一的平均高度
struct MouthStats {
    int pixels = 0;
    int dy     = 0;  // >0 表示右侧更低（顺时针歪）
};

static MouthStats analyze_mouth()
{
    MouthStats st;
    long sum_l = 0, n_l = 0, sum_r = 0, n_r = 0;
    int min_x = W, max_x = -1;

    // 只看嘴所在的区域，避免把脸颊（ShyDecorator）和气泡算进来
    constexpr int Y0 = 130, Y1 = 210, X0 = 60, X1 = 260;

    for (int y = Y0; y < Y1; y++) {
        for (int x = X0; x < X1; x++) {
            const uint16_t v = g_fb[y * W + x];
            const int r = ((v >> 11) & 0x1F) << 3;
            const int g = ((v >> 5) & 0x3F) << 2;
            const int b = (v & 0x1F) << 3;
            if (r > 200 && g > 200 && b > 200) {
                st.pixels++;
                if (x < min_x) min_x = x;
                if (x > max_x) max_x = x;
            }
        }
    }

    if (st.pixels == 0 || max_x < 0) {
        return st;
    }

    const int third = (max_x - min_x) / 3;
    for (int y = Y0; y < Y1; y++) {
        for (int x = min_x; x <= max_x; x++) {
            const uint16_t v = g_fb[y * W + x];
            const int r = ((v >> 11) & 0x1F) << 3;
            const int g = ((v >> 5) & 0x3F) << 2;
            const int b = (v & 0x1F) << 3;
            if (!(r > 200 && g > 200 && b > 200)) {
                continue;
            }
            if (x < min_x + third) {
                sum_l += y;
                n_l++;
            } else if (x > max_x - third) {
                sum_r += y;
                n_r++;
            }
        }
    }

    if (n_l > 0 && n_r > 0) {
        st.dy = (int)(sum_r / n_r) - (int)(sum_l / n_l);
    }
    return st;
}

static void snapshot(const std::string& out_path, const char* tag)
{
    const EyeStats eyes = [&] {
        EyeStats s;
        scan_images(lv_screen_active(), s);
        return s;
    }();
    const MouthStats mouth = analyze_mouth();

    printf("[%s] eye_images L=%d R=%d | mouth px=%d dy=%d\n", tag, eyes.left, eyes.right, mouth.pixels, mouth.dy);
    dump_ppm(out_path);
}

// ---------------------------------------------------------------- 场景

static void step_ms(Harness& h, uint32_t total_ms, uint32_t dt = 16)
{
    for (uint32_t t = 0; t < total_ms; t += dt) {
        h.step(dt);
    }
}

// 场景一：静置 12 秒，看眨眼 / 呼吸 / 待机微表情有没有互相打架
static void scenario_idle(lv_display_t* disp, Harness& h, const std::string& dir)
{
    h.add(std::make_unique<stackchan::BreathModifier>());
    h.add(std::make_unique<stackchan::BlinkModifier>());
    h.add(std::make_unique<stackchan::IdleExpressionModifier>());
    h.avatar().setEmotion(stackchan::avatar::Emotion::Neutral);

    for (int i = 0; i < 10; i++) {
        step_ms(h, 2500);
        render(disp);
        char tag[64];
        snprintf(tag, sizeof(tag), "idle %.1fs", (i + 1) * 2.5f);
        snapshot(dir + "/idle_" + std::to_string(i) + ".ppm", tag);
    }
}

// 场景二：摇晃（真机走 ImuEventModifier，会同时设 Dizzy 表情 + 加眩晕装饰器）
static void scenario_shake(lv_display_t* disp, Harness& h, const std::string& dir)
{
    h.add(std::make_unique<stackchan::BlinkModifier>());
    h.add(std::make_unique<stackchan::ImuEventModifier>());
    h.avatar().setEmotion(stackchan::avatar::Emotion::Neutral);

    step_ms(h, 1000);
    GetHAL().onImuMotionEvent.emit(ImuMotionEvent::Shake);

    for (int i = 0; i < 6; i++) {
        step_ms(h, 500);
        render(disp);
        char tag[64];
        snprintf(tag, sizeof(tag), "shake +%.1fs", (i + 1) * 0.5f);
        snapshot(dir + "/shake_" + std::to_string(i) + ".ppm", tag);
    }
}

// 场景三：摸头（按下 → 保持 → 松开）
static void scenario_pet(lv_display_t* disp, Harness& h, const std::string& dir)
{
    h.add(std::make_unique<stackchan::BlinkModifier>());
    h.add(std::make_unique<stackchan::HeadPetModifier>());
    h.avatar().setEmotion(stackchan::avatar::Emotion::Neutral);

    step_ms(h, 600);
    // 注意：HeadPetModifier 认的是"抚摸"（Swipe）和"松开"，Press 它不处理
    GetHAL().onHeadPetGesture.emit(HeadPetGesture::SwipeForward);
    step_ms(h, 300);
    GetHAL().onHeadPetGesture.emit(HeadPetGesture::SwipeForward);
    step_ms(h, 600);
    GetHAL().onHeadPetGesture.emit(HeadPetGesture::Release);

    for (int i = 0; i < 4; i++) {
        step_ms(h, 500);
        render(disp);
        char tag[64];
        snprintf(tag, sizeof(tag), "pet +%.1fs", (i + 1) * 0.5f);
        snapshot(dir + "/pet_" + std::to_string(i) + ".ppm", tag);
    }
}

// 场景四：说话 × 各种表情（这是"说话时嘴歪 / 方块错位"最容易露馅的地方）
static void scenario_speak(lv_display_t* disp, Harness& h, const std::string& dir,
                           stackchan::avatar::Emotion emotion, const char* name)
{
    h.avatar().setEmotion(emotion);
    step_ms(h, 100);
    h.add(std::make_unique<stackchan::SpeakingModifier>());

    for (int i = 0; i < 6; i++) {
        step_ms(h, 200);
        render(disp);
        char tag[64];
        snprintf(tag, sizeof(tag), "speak/%s %.1fs", name, (i + 1) * 0.2f);
        snapshot(dir + "/speak_" + name + "_" + std::to_string(i) + ".ppm", tag);
    }
}

// 场景五：摸头之后马上开始说话（Happy 表情 + SpeakingModifier 抢嘴）
static void scenario_pet_speak(lv_display_t* disp, Harness& h, const std::string& dir)
{
    h.add(std::make_unique<stackchan::BlinkModifier>());
    h.add(std::make_unique<stackchan::HeadPetModifier>());
    h.avatar().setEmotion(stackchan::avatar::Emotion::Neutral);

    step_ms(h, 600);
    GetHAL().onHeadPetGesture.emit(HeadPetGesture::SwipeForward);
    step_ms(h, 300);
    GetHAL().onHeadPetGesture.emit(HeadPetGesture::SwipeForward);
    step_ms(h, 600);

    h.add(std::make_unique<stackchan::SpeakingModifier>());
    for (int i = 0; i < 6; i++) {
        step_ms(h, 300);
        render(disp);
        char tag[64];
        snprintf(tag, sizeof(tag), "pet+speak %.1fs", (i + 1) * 0.3f);
        snapshot(dir + "/pet_speak_" + std::to_string(i) + ".ppm", tag);
    }
}

// 场景六：摇晃反应期间开始说话（两个 modifier 同时改嘴）
static void scenario_shake_speak(lv_display_t* disp, Harness& h, const std::string& dir)
{
    h.add(std::make_unique<stackchan::BlinkModifier>());
    h.add(std::make_unique<stackchan::ImuEventModifier>());
    h.avatar().setEmotion(stackchan::avatar::Emotion::Neutral);

    step_ms(h, 800);
    GetHAL().onImuMotionEvent.emit(ImuMotionEvent::Shake);
    step_ms(h, 600);

    h.add(std::make_unique<stackchan::SpeakingModifier>());
    for (int i = 0; i < 6; i++) {
        step_ms(h, 300);
        render(disp);
        char tag[64];
        snprintf(tag, sizeof(tag), "shake+speak %.1fs", (i + 1) * 0.3f);
        snapshot(dir + "/shake_speak_" + std::to_string(i) + ".ppm", tag);
    }
}

// ---------------------------------------------------------------- 嘴型原型

// 圆角矩形嘴（当前实现的做法）
static lv_obj_t* proto_rect(int cx, int cy, int w, int h, int r)
{
    lv_obj_t* o = lv_obj_create(lv_screen_active());
    lv_obj_remove_style_all(o);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_bg_color(o, lv_color_white(), 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(o, r, 0);
    lv_obj_set_size(o, w, h);
    lv_obj_align(o, LV_ALIGN_CENTER, cx - 160, cy - 120);
    return o;
}

// 厚月牙嘴：用 arc 的 indicator 粗细做出"张开的弯嘴"
static lv_obj_t* proto_crescent(int cx, int cy, int d, int width, int a0, int a1)
{
    lv_obj_t* arc = lv_arc_create(lv_screen_active());
    lv_obj_remove_style_all(arc);
    lv_obj_remove_flag(arc, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_arc_opa(arc, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_arc_opa(arc, LV_OPA_TRANSP, LV_PART_KNOB);
    lv_obj_set_style_bg_opa(arc, LV_OPA_TRANSP, LV_PART_KNOB);
    lv_obj_set_style_arc_color(arc, lv_color_white(), LV_PART_INDICATOR);
    lv_obj_set_style_arc_width(arc, width, LV_PART_INDICATOR);
    lv_obj_set_size(arc, d, d);
    lv_obj_align(arc, LV_ALIGN_CENTER, cx - 160, cy - 120);
    lv_arc_set_bg_angles(arc, 0, 360);
    lv_arc_set_angles(arc, a0, a1);
    return arc;
}

// D 形嘴：胶囊 + 一块直角矩形盖住上半（happy）或下半（sad），
// 得到"一边平、一边圆"的张口，没有月牙那种中缝缺口
static lv_obj_t* proto_d(int cx, int cy, int w, int h, bool happy)
{
    lv_obj_t* box = lv_obj_create(lv_screen_active());
    lv_obj_remove_style_all(box);
    lv_obj_remove_flag(box, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(box, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_bg_opa(box, LV_OPA_TRANSP, 0);
    lv_obj_set_size(box, w, h);
    lv_obj_align(box, LV_ALIGN_CENTER, cx - 160, cy - 120);

    auto add_white = [&](int bw, int bh, int radius, lv_align_t align, int dx, int dy) {
        lv_obj_t* o = lv_obj_create(box);
        lv_obj_remove_style_all(o);
        lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_remove_flag(o, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_style_bg_color(o, lv_color_white(), 0);
        lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
        lv_obj_set_style_radius(o, radius, 0);
        lv_obj_set_size(o, bw, bh);
        lv_obj_align(o, align, dx, dy);
    };

    // 胶囊本体
    add_white(w, h, h / 2, LV_ALIGN_CENTER, 0, 0);
    // 盖掉一半的圆角：happy 盖上半（得到平顶圆底），sad 盖下半（平底圆顶）
    add_white(w + 2, h / 2, 0, happy ? LV_ALIGN_TOP_MID : LV_ALIGN_BOTTOM_MID, 0, 0);
    return box;
}

// ---------------------------------------------------------------- 主入口

int main(int argc, char** argv)
{
    const std::string out_dir = (argc > 1) ? argv[1] : "preview/lvsim";
    const std::string mode    = (argc > 2) ? argv[2] : "scenario";

    lv_init();
    lv_tick_set_cb([]() -> uint32_t { return g_ms; });

    lv_display_t* disp = lv_display_create(W, H);
    lv_display_set_color_format(disp, LV_COLOR_FORMAT_RGB565);

    static uint8_t draw_buf[W * 40 * 2];
    lv_display_set_buffers(disp, draw_buf, nullptr, sizeof(draw_buf), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(disp, flush_cb);

    lv_obj_t* scr = lv_screen_active();
    lv_obj_set_style_bg_color(scr, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);
    lv_obj_remove_flag(scr, LV_OBJ_FLAG_SCROLLABLE);

    if (mode == "mouth-proto") {
        // 说话时嘴型的候选方案，套在 happy / sad 的真实脸部上下文里对比
        struct Cell {
            const char* file;
            stackchan::avatar::Emotion emotion;
            int variant;  // 0=方块/胶囊（现状） 1=厚月牙 2=薄月牙
        };
        const Cell cells[] = {
            {"proto_happy_0", stackchan::avatar::Emotion::Happy, 0},
            {"proto_happy_1", stackchan::avatar::Emotion::Happy, 1},
            {"proto_happy_2", stackchan::avatar::Emotion::Happy, 2},
            {"proto_happy_3", stackchan::avatar::Emotion::Happy, 3},
            {"proto_sad_0", stackchan::avatar::Emotion::Sad, 0},
            {"proto_sad_1", stackchan::avatar::Emotion::Sad, 1},
            {"proto_sad_2", stackchan::avatar::Emotion::Sad, 2},
            {"proto_sad_3", stackchan::avatar::Emotion::Sad, 3},
        };

        for (const auto& c : cells) {
            Harness h;
            h.setup();
            h.avatar().setEmotion(c.emotion);
            h.avatar().mouth().setWeight(60);   // 说话中
            h.avatar().mouth().setVisible(false);  // 先藏掉真嘴，避免和原型重叠
            for (int i = 0; i < 4; i++) {
                h.step(16);
            }

            const bool happy = (c.emotion == stackchan::avatar::Emotion::Happy);
            const int cx = 160, cy = 158;
            lv_obj_t* proto_obj = nullptr;

            if (c.variant == 0) {
                // 现状：happy 是满圆角胶囊，sad 是偏方的块
                proto_obj = proto_rect(cx, cy, 72, 34, happy ? 17 : 3);
            } else if (c.variant == 1) {
                // 扁平月牙：d 拉大让中缝缺口变浅
                proto_obj = proto_crescent(cx, happy ? cy - 22 : cy + 22, 110, 22, happy ? 22 : 202,
                                           happy ? 158 : 338);
            } else if (c.variant == 2) {
                // 薄月牙
                proto_obj = proto_crescent(cx, happy ? cy - 24 : cy + 24, 88, 14, happy ? 20 : 200,
                                           happy ? 160 : 340);
            } else {
                // D 形：happy 平顶圆底，sad 平底圆顶
                proto_obj = proto_d(cx, cy, 64, 34, happy);
            }

            render(disp);
            snapshot(out_dir + "/" + c.file + ".ppm", c.file);

            // 只删这一格画上去的原型对象；头像由 Harness 析构时自己清
            if (proto_obj) {
                lv_obj_delete(proto_obj);
            }
        }
        return 0;
    }

    if (mode == "blink-strip") {
        // 眼睑权重扫描：看眨眼过程中眼睛还剩多少
        Harness h;
        h.setup();
        h.avatar().setEmotion(stackchan::avatar::Emotion::Neutral);

        const int weights[] = {100, 75, 50, 25, 12, 0};
        for (int w : weights) {
            h.avatar().leftEye().setWeight(w);
            h.avatar().rightEye().setWeight(w);
            for (int i = 0; i < 3; i++) {
                h.step(16);
            }
            render(disp);

            // 量左眼区域里可见的白色像素（y 从 74 起，避开眉毛）
            int y0 = -1, y1 = -1, px = 0;
            for (int y = 74; y < 146; y++) {
                for (int x = 70; x < 146; x++) {
                    const uint16_t v = g_fb[y * W + x];
                    const int r = ((v >> 11) & 0x1F) << 3;
                    const int g = ((v >> 5) & 0x3F) << 2;
                    const int b = (v & 0x1F) << 3;
                    if (r > 200 && g > 200 && b > 200) {
                        px++;
                        if (y0 < 0) y0 = y;
                        y1 = y;
                    }
                }
            }
            printf("[blink w=%3d] 左眼白像素 %4d，高度 %d px\n", w, px, (y0 < 0) ? 0 : (y1 - y0 + 1));
            dump_ppm(out_dir + "/blink_" + std::to_string(w) + ".ppm");
        }
        return 0;
    }

    if (mode == "mouth-sweep") {
        // 说话时的嘴型对比：happy 和 laugh 在"静息 / 说话停顿帧 / 说话张口帧"三态下是否一致。
        // 停顿帧 = SpeakingModifier 把权重抖回 0~8 的那一下，是最容易露馅的地方。
        struct Case {
            const char* name;
            stackchan::avatar::Emotion emotion;
        };
        const Case cases[] = {
            {"happy", stackchan::avatar::Emotion::Happy},
            {"laugh", stackchan::avatar::Emotion::Laugh},
        };

        auto settle = [](Harness& h, int frames) {
            for (int i = 0; i < frames; i++) {
                h.step(16);
            }
        };
        auto shoot = [&](Harness& h, const std::string& path, const char* tag) {
            render(disp);
            dump_ppm(path);
            printf("[sweep %s]\n", tag);
        };

        for (const auto& c : cases) {
            Harness h;
            h.setup();
            // 真机由显示主题给的米白底；渲染器里默认黑底，这里补上以贴近实机
            lv_obj_set_style_bg_color(lv_screen_active(), lv_color_hex(0xEDE7DC), 0);
            lv_obj_set_style_bg_opa(lv_screen_active(), LV_OPA_COVER, 0);
            h.avatar().setEmotion(c.emotion);
            h.avatar().mouth().setPosition(uitk::Vector2i(0, 0));

            // 1) 静息（从没说过话）
            h.avatar().mouth().setWeight(0);
            settle(h, 8);
            shoot(h, out_dir + "/sweep_" + c.name + "_rest.ppm", c.name);

            // 2) 说话中：先张到 60，再过几帧掉回 0（停顿帧）
            h.avatar().mouth().setWeight(60);
            settle(h, 6);
            h.avatar().mouth().setWeight(0);
            settle(h, 3);
            shoot(h, out_dir + "/sweep_" + c.name + "_pause.ppm", c.name);

            // 3) 说话中张口帧
            h.avatar().mouth().setWeight(60);
            settle(h, 6);
            shoot(h, out_dir + "/sweep_" + c.name + "_talk.ppm", c.name);
        }
        return 0;
    }

    if (mode == "timer") {
        // 番茄钟屏：信息带 / 引线（25/50/75/92%）/ 超时。
        // 引线是 LVGL 图元画的，这里出的图和真机走同一份代码。
        struct Case {
            const char* name;
            int kind;  // 0=band 1=fuse 2=overtime 3=panel
            int ms;
            int progress;
            const char* tag;
        };
        const Case cases[] = {
            {"band", 0, 24 * 60 * 1000 + 37 * 1000, 2, "信息带 / 2%"},
            {"band50", 0, 12 * 60 * 1000 + 30 * 1000, 50, "信息带 / 50%"},
            {"band95", 0, 70 * 1000, 95, "信息带 / 95%"},
            {"fuse80", 1, 5 * 60 * 1000, 5 * 60 * 1000, "引线 · 刚切入（0%）"},
            {"fuse88", 1, 3 * 60 * 1000, 5 * 60 * 1000, "引线 · 40%"},
            {"fuse95", 1, 70 * 1000, 5 * 60 * 1000, "引线 · 77%"},
            {"fuse98", 1, 18 * 1000, 5 * 60 * 1000, "引线 · 94%"},
            {"overtime", 2, 2 * 60 * 1000 + 15 * 1000, 100, "超时 +02:15"},
            {"panel", 3, 3 * 60 * 1000 + 12 * 1000, 42, "长按抽屉"},
        };
        for (const auto& c : cases) {
            Harness h;
            h.setup();
            // 真机由显示主题给的米白底；渲染器里默认黑底，这里补上以贴近实机
            lv_obj_set_style_bg_color(lv_screen_active(), lv_color_hex(0xEDE7DC), 0);
            lv_obj_set_style_bg_opa(lv_screen_active(), LV_OPA_COVER, 0);
            h.avatar().setEmotion(stackchan::avatar::Emotion::Neutral);
            // 用 20px 字体模拟真机的中文字号，否则信息带行高比实机小一号，看不出裁切
            auto overlay = std::make_unique<stackchan::avatar::DefaultCountdownOverlay>(
                nullptr, 320, 240, &lv_font_montserrat_20);
            if (c.kind == 0) {
                overlay->showBand(c.ms, "FOCUS", "write the prototype", c.progress);
            } else if (c.kind == 1) {
                // 副标题跟真机一致：放任务名，不放"剩余多少"
                // 多推几拍，让火星在静帧里也看得见（真机是 8Hz 连续跑）
                for (int k = 0; k < 5; k++) {
                    overlay->showFuse(c.ms, c.progress, "write prototype");
                }
            } else if (c.kind == 2) {
                overlay->showOvertime(c.ms);
            } else {
                overlay->showBand(c.ms, "FOCUS", "write the prototype", c.progress);
                overlay->showPanel("FOCUS", c.ms);
                // 抽屉是坐标命中的（LVGL 收不到对象点击），这里把关键点过一遍
                using Hit = stackchan::avatar::DefaultCountdownOverlay::PanelHit;
                struct {
                    int x, y;
                    Hit want;
                } probes[] = {
                    {84, 181, Hit::Complete},    // 左按钮中心
                    {236, 181, Hit::Abandon},    // 右按钮中心
                    {14, 158, Hit::Complete},    // 左按钮左上角（边界内）
                    {153, 203, Hit::Complete},   // 左按钮右下角（边界内）
                    {155, 181, Hit::None},       // 两按钮之间的缝隙
                    {160, 220, Hit::Continue},   // 「继续专注」
                    {60, 80, Hit::Continue},     // 抽屉上方的脸
                    {160, 140, Hit::None},       // 标题行空白
                };
                for (auto& p : probes) {
                    const auto got = overlay->panelHitTest(p.x, p.y);
                    printf("[hit] (%3d,%3d) got=%d want=%d %s\n", p.x, p.y, (int)got, (int)p.want,
                           got == p.want ? "OK" : "MISMATCH");
                }
            }
            step_ms(h, 32);
            render(disp);
            snapshot(out_dir + "/timer_" + c.name + ".ppm", c.tag);
        }
        return 0;
    }

    if (mode == "symbols") {
        // 漫画符号原型：疑惑配问号、惊讶配感叹号。
        // 每种符号出两档位置（默认 / 贴近脸），各抓"弹出瞬间"和"浮动中"两帧。
        struct Case {
            const char* name;
            stackchan::avatar::Emotion emotion;
            const lv_image_dsc_t* src;
        };
        const Case cases[] = {
            {"doubt", stackchan::avatar::Emotion::Doubt, &decorator_question},
            {"surprised", stackchan::avatar::Emotion::Surprised, &decorator_exclam},
        };
        const int positions[][2] = {{116, -74}, {96, -56}};

        for (const auto& c : cases) {
            for (int p = 0; p < 2; p++) {
                Harness h;
                h.setup();
                h.avatar().setEmotion(c.emotion);

                auto dec = std::make_unique<stackchan::avatar::SymbolDecorator>(lv_screen_active(), c.src);
                dec->setPosition(positions[p][0], positions[p][1]);
                h.avatar().addDecorator(std::move(dec));

                for (int i = 0; i < 6; i++) {
                    h.step(16);
                }
                render(disp);
                dump_ppm(out_dir + "/symbol_" + c.name + "_" + std::to_string(p) + "_pop.ppm");

                for (int i = 0; i < 60; i++) {
                    h.step(16);
                }
                render(disp);
                dump_ppm(out_dir + "/symbol_" + c.name + "_" + std::to_string(p) + "_bob.ppm");
                printf("[symbols] %s pos=%d,%d\n", c.name, positions[p][0], positions[p][1]);
            }
        }
        return 0;
    }

    if (mode == "gallery") {
        // 九种表情 + 各自会挂的配饰，一张图看全
        struct Entry {
            const char* name;
            stackchan::avatar::Emotion emotion;
            int deco;  // 0=无 1=问号 2=感叹号 3=青筋 4=汗滴
        };
        const Entry entries[] = {
            {"neutral", stackchan::avatar::Emotion::Neutral, 0},
            {"happy", stackchan::avatar::Emotion::Happy, 8},
            {"laugh", stackchan::avatar::Emotion::Laugh, 8},
            {"angry", stackchan::avatar::Emotion::Angry, 3},
            {"sad", stackchan::avatar::Emotion::Sad, 9},
            {"doubt", stackchan::avatar::Emotion::Doubt, 1},
            {"sleepy", stackchan::avatar::Emotion::Sleepy, 10},
            {"surprised", stackchan::avatar::Emotion::Surprised, 2},
            {"dizzy", stackchan::avatar::Emotion::Dizzy, 0},
            // 事件类配饰（不是由情绪触发，单独列出来看效果）
            {"event_pet", stackchan::avatar::Emotion::Happy, 6},
            {"event_shake", stackchan::avatar::Emotion::Dizzy, 7},
        };

        for (const auto& e : entries) {
            Harness h;
            h.setup();
            h.avatar().setEmotion(e.emotion);

            switch (e.deco) {
                case 1:
                    h.avatar().addDecorator(
                        std::make_unique<stackchan::avatar::SymbolDecorator>(lv_screen_active(), &decorator_question));
                    break;
                case 2:
                    h.avatar().addDecorator(
                        std::make_unique<stackchan::avatar::SymbolDecorator>(lv_screen_active(), &decorator_exclam));
                    break;
                case 3:
                    h.avatar().addDecorator(std::make_unique<stackchan::avatar::AngryDecorator>(lv_screen_active(), 0));
                    break;
                case 4:
                    h.avatar().addDecorator(std::make_unique<stackchan::avatar::SweatDecorator>(lv_screen_active(), 0));
                    break;
                case 5:
                    h.avatar().addDecorator(std::make_unique<stackchan::avatar::DizzyDecorator>(lv_screen_active(), 0, 0));
                    break;
                case 6:
                    h.avatar().addDecorator(std::make_unique<stackchan::avatar::HeartDecorator>(lv_screen_active(), 0, 500));
                    h.avatar().addDecorator(std::make_unique<stackchan::avatar::ShyDecorator>(lv_screen_active(), 0));
                    break;
                case 7:
                    h.avatar().addDecorator(std::make_unique<stackchan::avatar::ShyDecorator>(lv_screen_active(), 0));
                    break;
                case 8:
                    h.avatar().addDecorator(std::make_unique<stackchan::avatar::HeartDecorator>(lv_screen_active(), 0, 500));
                    break;
                case 9:
                    h.avatar().addDecorator(std::make_unique<stackchan::avatar::TearDecorator>(lv_screen_active(), 0));
                    break;
                case 10:
                    h.avatar().addDecorator(std::make_unique<stackchan::avatar::ZzzDecorator>(lv_screen_active(), 0));
                    break;
                default:
                    break;
            }

            for (int i = 0; i < 45; i++) {
                h.step(16);
            }
            render(disp);
            dump_ppm(out_dir + "/gallery_" + std::string(e.name) + ".ppm");
            printf("[gallery] %s deco=%d\n", e.name, e.deco);
        }
        return 0;
    }

    if (mode == "tree") {
        // 只初始化一次，打一遍对象树，方便对照结构
        Harness h;
        h.setup();
        h.avatar().setEmotion(stackchan::avatar::Emotion::Dizzy);
        printf("==== dizzy 表情的完整对象树 ====\n");
        dump_tree_rec(lv_screen_active(), 0);
        return 0;
    }

    if (mode == "shake-tree") {
        // 摇晃触发后的对象树：用来确认"每侧多个 image"到底是哪几个
        Harness h;
        h.setup();
        h.add(std::make_unique<stackchan::BlinkModifier>());
        h.add(std::make_unique<stackchan::ImuEventModifier>());
        h.avatar().setEmotion(stackchan::avatar::Emotion::Neutral);
        step_ms(h, 1000);
        GetHAL().onImuMotionEvent.emit(ImuMotionEvent::Shake);
        step_ms(h, 500);
        render(disp);
        printf("==== 摇晃 +0.5s 的对象树 ====\n");
        dump_tree_rec(lv_screen_active(), 0);
        snapshot(out_dir + "/shake_tree.ppm", "shake-tree");
        return 0;
    }

    if (mode == "matrix") {
        Harness h;
        h.setup();
        struct Item {
            const char* name;
            stackchan::avatar::Emotion emotion;
        };
        const Item items[] = {
            {"neutral", stackchan::avatar::Emotion::Neutral},   {"happy", stackchan::avatar::Emotion::Happy},
            {"laugh", stackchan::avatar::Emotion::Laugh},       {"angry", stackchan::avatar::Emotion::Angry},
            {"sad", stackchan::avatar::Emotion::Sad},           {"doubt", stackchan::avatar::Emotion::Doubt},
            {"sleepy", stackchan::avatar::Emotion::Sleepy},     {"surprised", stackchan::avatar::Emotion::Surprised},
            {"dizzy", stackchan::avatar::Emotion::Dizzy},
        };
        struct State {
            const char* name;
            int mouth_weight;
            int eye_weight;
        };
        // eye_weight = -1 表示"不干预"，保留表情自己给的眼睛权重。
        // 之前这里固定写 100，等于把所有表情的眼睛强行拉成全开：
        // sleepy（24）、angry（56）、sad（84）在矩阵里都变成了大圆眼，
        // 看起来就像"说完话眼睛睁开了"，其实是这列参数的锅。
        // blink = 真机眨眼时 BlinkModifier 实际给的值（25）
        // 不再出"权重 0"那一列：那是 _closed_line，一条直线，真机走不到，看着也容易误判
        const State states[] = {
            {"rest", 0, -1},
            {"talk", 60, -1},
            {"blink", 0, 25},
        };
        for (const auto& it : items) {
            for (const auto& st : states) {
                h.step(16);
                h.avatar().setEmotion(it.emotion);
                h.avatar().mouth().setPosition(uitk::Vector2i(0, 0));
                h.avatar().leftEye().setPosition(uitk::Vector2i(0, 0));
                h.avatar().rightEye().setPosition(uitk::Vector2i(0, 0));
                h.avatar().mouth().setRotation(0);
                if (st.eye_weight >= 0) {
                    h.avatar().leftEye().setWeight(st.eye_weight);
                    h.avatar().rightEye().setWeight(st.eye_weight);
                }
                h.avatar().mouth().setWeight(st.mouth_weight);
                for (int i = 0; i < 6; i++) {
                    h.step(16);
                }
                render(disp);
                char tag[64];
                snprintf(tag, sizeof(tag), "%s/%s", it.name, st.name);
                snapshot(out_dir + "/" + it.name + "_" + st.name + ".ppm", tag);
            }
        }
        return 0;
    }

    {
        Harness h;
        h.setup();
        scenario_idle(disp, h, out_dir);
    }
    {
        Harness h;
        h.setup();
        scenario_shake(disp, h, out_dir);
    }
    {
        Harness h;
        h.setup();
        scenario_pet(disp, h, out_dir);
    }
    {
        // 每个表情单独跑一轮"说话"，避免上一轮残留
        const std::pair<stackchan::avatar::Emotion, const char*> speak_cases[] = {
            {stackchan::avatar::Emotion::Neutral, "neutral"},
            {stackchan::avatar::Emotion::Happy, "happy"},
            {stackchan::avatar::Emotion::Laugh, "laugh"},
            {stackchan::avatar::Emotion::Angry, "angry"},
            {stackchan::avatar::Emotion::Sad, "sad"},
            {stackchan::avatar::Emotion::Doubt, "doubt"},
            {stackchan::avatar::Emotion::Sleepy, "sleepy"},
            {stackchan::avatar::Emotion::Surprised, "surprised"},
            {stackchan::avatar::Emotion::Dizzy, "dizzy"},
        };
        for (auto& c : speak_cases) {
            Harness h;
            h.setup();
            scenario_speak(disp, h, out_dir, c.first, c.second);
        }
    }
    {
        Harness h;
        h.setup();
        scenario_pet_speak(disp, h, out_dir);
    }
    {
        Harness h;
        h.setup();
        scenario_shake_speak(disp, h, out_dir);
    }

    printf("完成\n");
    return 0;
}
