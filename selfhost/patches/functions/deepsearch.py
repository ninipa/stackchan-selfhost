"""DeepSeek Search 联网检索插件。

设计依据见 `docs/agent-review-2026-11-08-v2.md` §5.4：
- 请求 count=6（多取不加钱，搜索按次计费）
- 按 rerank_score 降序，取 top3 整条保留
- 只取 summary 不拼 snippet（避免重复）
- 总量封顶 10k，超过则整条丢弃，不做条内截断

挂载方式：`selfhost/patches/functions/deepsearch.py` →
`plugins_func/functions/deepsearch.py`（只读）。插件目录是扫描发现的，
新增文件即自动注册，不需要覆盖上游任何文件。
"""

import httpx
from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

TOP_N = 3
MAX_CHARS = 10000

DEEPSEARCH_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "deepsearch",
        "description": (
            "联网搜索。用户问新闻、时事、赛事、价格、产品发布、人物近况等"
            "实时或你不确定的信息时，必须调用此工具，不要凭记忆回答。"
            "闲聊、情感陪伴、常识不需要调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，保留用户原问题里的关键实体和时间限定",
                }
            },
            "required": ["query"],
        },
    },
}


async def _call_search(api_key: str, query: str, count: int) -> list:
    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=3.0)) as client:
        resp = await client.post(
            "https://api.deepseek.com/v1/tools/search",
            json={"query": query, "count": count},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:150]}")
    return resp.json().get("results", [])


def _format(results: list) -> str:
    ordered = sorted(results, key=lambda x: -(x.get("rerank_score") or 0))
    blocks, total = [], 0
    for i, item in enumerate(ordered[:TOP_N], 1):
        summary = (item.get("summary") or item.get("snippet") or "").strip()
        date = (item.get("publish_date") or "")[:10]
        head = f"{i}. {item.get('title', '')}"
        if date:
            head += f"（{date}）"
        block = f"{head}\n{summary}"
        if blocks and total + len(block) > MAX_CHARS:
            break
        blocks.append(block)
        total += len(block)
    return "\n".join(blocks)


@register_function("deepsearch", DEEPSEARCH_FUNCTION_DESC, ToolType.SYSTEM_CTL)
async def deepsearch(conn: "ConnectionHandler", query: str = None):
    if not query:
        return ActionResponse(Action.REQLLM, "请提供搜索关键词。", None)

    cfg = conn.config.get("plugins", {}).get("deepsearch", {}) or {}
    api_key = cfg.get("api_key") or ""
    if not api_key:
        # 默认复用 LLM 的 key，配置里不用重复填
        api_key = (
            conn.config.get("LLM", {})
            .get(conn.config.get("selected_module", {}).get("LLM", ""), {})
            .get("api_key", "")
        )
    if not api_key or api_key.startswith("你的"):
        logger.bind(tag=TAG).error("deepsearch 未配置 API Key")
        return ActionResponse(Action.REQLLM, "联网搜索还没有配置好，先回答你知道的。", None)

    count = int(cfg.get("count", 6))
    try:
        results = await _call_search(api_key, query, count)
    except httpx.TimeoutException:
        logger.bind(tag=TAG).error(f"搜索超时 | query={query}")
        return ActionResponse(Action.REQLLM, "联网搜索超时了，稍后再试。", None)
    except Exception as e:
        logger.bind(tag=TAG).error(f"搜索失败 | query={query} | {e}")
        return ActionResponse(Action.REQLLM, "联网搜索失败了，稍后再试。", None)

    if not results:
        logger.bind(tag=TAG).info(f"搜索无结果 | query={query}")
        return ActionResponse(Action.REQLLM, "没有搜到相关结果。", None)

    text = _format(results)
    logger.bind(tag=TAG).info(
        f"搜索完成 | query={query} | 返回{len(results)}条 | 注入{len(text)}字"
    )
    return ActionResponse(Action.REQLLM, text, None)
