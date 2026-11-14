#!/usr/bin/env bash
# 检查 LLM 是否遵守「每条回复开头只放一个 emoji」的规则。
#
# 这是情绪→表情联动的前半段：只有 LLM 稳定吐 emoji，服务端才会发 llm.emotion，
# 固件才会换表情。换模型、改 prompt、改基础模板后跑一次即可。
#
# 用法：
#   ./check_llm_emoji.sh                      # 用默认五句
#   ./check_llm_emoji.sh "你好呀" "我好累哦"   # 自定义
set -euo pipefail

CONTAINER="${CONTAINER:-xiaozhi-esp32-server}"

PHRASES=("$@")
if [ ${#PHRASES[@]} -eq 0 ]; then
  PHRASES=("你好呀" "我今天好累哦" "你猜我在干嘛" "讲个冷笑话" "我好难过")
fi
PHRASE_JSON=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:], ensure_ascii=False))' "${PHRASES[@]}")

docker exec -i "$CONTAINER" python - "$PHRASE_JSON" <<'PY'
import json
import re
import sys
import urllib.request

import yaml
from jinja2 import Template

phrases = json.loads(sys.argv[1])

cfg = yaml.safe_load(open("/opt/xiaozhi-esp32-server/data/.config.yaml"))
llm = cfg["LLM"]["DeepSeekLLM"]
tpl = open("/opt/xiaozhi-esp32-server/agent-base-prompt.txt").read()

# 与 prompt_manager.py 的 EMOJI_List 保持一致
EMOJIS = ["😶", "🙂", "😆", "😂", "😔", "😠", "😭", "😍", "😳", "😲", "😱",
          "🤔", "😉", "😎", "😌", "🤤", "😘", "😏", "😴", "😜", "🙄"]
EMOJI_RE = re.compile("^(" + "|".join(EMOJIS) + ")")

system_prompt = Template(tpl).render(
    base_prompt=cfg["prompt"],
    current_time="2026-11-06 09:00",
    today_date="2026-11-06",
    today_weekday="星期五",
    lunar_date="九月十七",
    local_address="",
    weather_info="",
    dynamic_context="",
    language="中文",
    emoji_enabled=True,
    emojiList=EMOJIS,
)


def ask(text: str) -> str:
    body = {
        "model": llm["model_name"],
        "temperature": llm.get("temperature", 0.7),
        "max_tokens": llm.get("max_tokens", 500),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    }
    req = urllib.request.Request(
        llm["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + llm["api_key"],
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"]


misses = 0
for q in phrases:
    answer = ask(q)
    match = EMOJI_RE.match(answer)
    if match:
        print(f"OK   {match.group(1)}  {q}  ->  {answer[:40]!r}")
    else:
        misses += 1
        print(f"MISS -  {q}  ->  {answer[:40]!r}")

print(f"\n合规 {len(phrases) - misses}/{len(phrases)}")
sys.exit(1 if misses else 0)
PY
