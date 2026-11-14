/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once
#include "elements/key_elements.h"
#include "decorator.h"
#include <esp_log.h>
#include <memory>

namespace stackchan::avatar {

/**
 * @brief Avatar base class
 *
 */
class Avatar {
public:
    virtual ~Avatar() = default;

    /**
     * @brief Update avatar, trigger all elements, decorators and modifiers to update
     *
     */
    virtual void update()
    {
        _key_elements.forEach([](Element* element) {
            // Update all elements
            element->_update();
        });

        _decorator_pool.forEach([this](Decorator* decorator, int id) {
            // Update all decorators
            decorator->_update();
        });

        // Cleanup pools
        _decorator_pool.cleanup();
    }

    const KeyElements_t& getKeyElements()
    {
        return _key_elements;
    }

    virtual void setEmotion(const Emotion& emotion)
    {
        _emotion = emotion;
        ++_emotion_version;  // 每次设置都自增，供临时表情修饰器判断"有没有被别人改过"

        // 表情的唯一收口：任何绕过 display->SetEmotion 的写入（修饰器、未来的新代码）
        // 都会在这里留下痕迹。排查"脸莫名变了"时，只有这一行而前面没有
        // "SetEmotion: xxx" 的，就是直接写 avatar 的人干的。
        ESP_LOGI("Avatar", "emotion <- %d (v%u)", static_cast<int>(emotion),
                 static_cast<unsigned>(_emotion_version));

        _key_elements.forEach([&emotion](Element* element) {
            // Set for all elements
            element->setEmotion(emotion);
        });

        _decorator_pool.forEach([&emotion](Decorator* decorator, int id) {
            // Set for all decorators
            decorator->setEmotion(emotion);
        });
    }

    Emotion getEmotion() const
    {
        return _emotion;
    }

    /** 情绪版本号：修饰器记下自己设表情后的版本，还原前比对。
     *  期间只要有人改过表情（对话、番茄钟、情绪复位）就不再还原，
     *  避免把几秒前的旧表情盖回去。 */
    uint32_t getEmotionVersion() const
    {
        return _emotion_version;
    }

    Feature& leftEye()
    {
        return *getKeyElements().leftEye;
    }

    Feature& rightEye()
    {
        return *getKeyElements().rightEye;
    }

    Feature& mouth()
    {
        return *getKeyElements().mouth;
    }

    void setSpeech(std::string_view text)
    {
        if (getKeyElements().speechBubble) {
            getKeyElements().speechBubble->setSpeech(text);
        }
    }

    void clearSpeech()
    {
        if (getKeyElements().speechBubble) {
            getKeyElements().speechBubble->clearSpeech();
        }
    }

    void setSpeechTextFont(void* font)
    {
        if (getKeyElements().speechBubble) {
            getKeyElements().speechBubble->setTextFont(font);
        }
    }

    void setModifyLock(bool locked)
    {
        _is_modify_locked = locked;
    }

    bool isModifyLocked()
    {
        return _is_modify_locked;
    }

    /* ---------------------------- Decorator helpers --------------------------- */

    int addDecorator(std::unique_ptr<Decorator> decorator)
    {
        return _decorator_pool.create(std::move(decorator));
    }

    bool removeDecorator(int id)
    {
        return _decorator_pool.destroy(id);
    }

    // 用于外部持有 id 的调用方确认装饰器还活着（例如被 clearDecorators() 清掉之后）
    Decorator* getDecorator(int id)
    {
        return _decorator_pool.get(id);
    }

    void clearDecorators()
    {
        _decorator_pool.clear();
    }

protected:
    Avatar() = default;

    Emotion _emotion = Emotion::Neutral;
    uint32_t _emotion_version = 0;
    KeyElements_t _key_elements;
    ObjectPool<Decorator> _decorator_pool;

    bool _is_modify_locked = false;
};

}  // namespace stackchan::avatar
