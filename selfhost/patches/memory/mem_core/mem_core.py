"""mem_core：结构化记忆 provider（档案常驻 + 事件检索）。

设计原则见 docs/memory-design-2026-11-09.md，核心是三层：

- 档案 profile：稳定事实（称呼/城市/长期偏好），覆盖式更新，常驻注入
- 事件 events：具体的事/承诺/项目，带日期和标签，按 query 检索 top-k
- 会话：不归这里管，连接内的逐字历史由 Dialogue 负责

写入只在连接关闭时发生一次（LLM 抽取），读取是纯本地匹配，不调 LLM。

配置项（config["Memory"]["mem_core"]）：
    memory_path        存储文件，默认 data/memory.json
    max_profile_chars  档案总长度上限，默认 800
    max_events         事件条数上限，默认 200
    ttl_days           事件过期天数，默认 365（0 = 不过期）
    retrieve_top_k     检索注入条数，默认 5
    inject_max_chars   注入文本总长度上限，默认 1000
    min_user_turns     门槛：用户轮数低于此值才可能跳过，默认 2
    min_user_chars     门槛：单轮且用户总字数低于此值才跳过，默认 8（只拦"嗯""好的"）
    compact_threshold  事件超过此条数触发压缩，默认 150（0 = 不压缩）
    enabled            总开关，默认 true
"""

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from config.config_loader import get_project_dir

from ..base import MemoryProviderBase, logger

TAG = __name__

DEFAULT_MEMORY_PATH = "data/memory.json"

# 档案只允许这些键，避免同义词堆积（城市/所在地/常住地）。
# 白名单外的键不再丢弃，而是转成一条事件（见 _merge），保证信息不丢。
PROFILE_KEYS = [
    "称呼",
    "城市",
    "时区",
    "语言",
    "家人",
    "宠物",
    "职业",
    "饮食偏好",
    "兴趣爱好",
    "健康状况",
    "作息",
    "纪念日",
    "回答偏好",
]

# 模型可能输出的同义键，统一映射到白名单
PROFILE_KEY_ALIASES = {
    "所在地": "城市",
    "常住地": "城市",
    "居住地": "城市",
    "地址": "城市",
    "位置": "城市",
    "名字": "称呼",
    "昵称": "称呼",
    "姓名": "称呼",
    "回答风格": "回答偏好",
    "风格": "回答偏好",
    "偏好": "回答偏好",
    "语言偏好": "语言",
    "口味": "饮食偏好",
    "饮食": "饮食偏好",
    "爱吃": "饮食偏好",
    "忌口": "饮食偏好",
    "爱好": "兴趣爱好",
    "兴趣": "兴趣爱好",
    "健康": "健康状况",
    "过敏": "健康状况",
    "作息习惯": "作息",
    "生日": "纪念日",
    "口味偏好": "饮食偏好",
    "健康状态": "健康状况",
}

# 问「记忆里有什么」这类元问题不含事件关键词，靠它触发最近事件注入
META_QUERY_WORDS = (
    "记忆",
    "记得",
    "记住",
    "复述",
    "哪些事",
    "什么事",
    "说过",
    "告诉过",
    "聊过",
)

# 抽取提示词：只认用户陈述/确认，助手的话只作上下文。
EXTRACT_PROMPT = """你是记忆抽取器。只做一件事：从对话中抽取"值得长期记住"的信息。

规则：
1. 只抽取用户陈述或明确确认的内容。用户说"对/是的/没错"确认的助手结论，视为用户认可的事实：
   档案类信息写进 profile；具体结论或进展写成一条事件（直接写结论的陈述句，
   不要以"用户说/用户确认"开头，也不要写成问句）。
2. assistant 的话只用于理解上下文，不得作为事实来源。assistant 的承诺单独记为事件。
3. 不记：提问（"我叫什么"不是事实）、寒暄、一次性查询（天气/时间/新闻/笑话）、
   临时情绪（"今天好累"），除非用户明确表达长期偏好。
   用户明确要求「记住」的内容一律记：偏好进 profile，具体的事/计划进 events。
4. profile 只放稳定事实，键只能从这些里选（没有合适的就写成事件，不要自造键）：
   称呼、城市、时区、语言、家人、宠物、职业、饮食偏好、兴趣爱好、健康状况、作息、纪念日、回答偏好。
   值必须是短语（谁 / 是什么），≤50 字；不写时间、不写计划、不写过程——这些归 events。
5. events 放具体的事、承诺、项目，每条 ≤40 字，带 1-3 个标签。
   **相对时间必须换算成绝对日期**：按下面给出的"今天"换算，写「11月10日」而不是「明天」；
   文本里不要出现今天/明天/后天/下周/下个月这类词——它们过期后会变成错误信息。
6. 事件若有明确的截止或发生日期，把它填进 due（YYYY-MM-DD）；没有就填空字符串 ""。
   例：「豆包 11 月 10 日期中考试」→ due: "2026-11-10"；「在弄 stackchan 的语音链路」→ due: ""。
7. 新事实与旧档案冲突时，直接输出新值（会覆盖旧值）。
8. 如果用户表示某件旧事已完成，把它的关键词放进 done。

只输出 JSON，不要解释，不要 markdown 代码块：
{
  "profile": {"字段名": "值"},
  "events": [{"text": "事件描述", "tags": ["标签"], "status": "open", "due": "YYYY-MM-DD 或 空"}],
  "done": ["已完成事件的关键词"]
}

没有可记的内容时输出：{"profile": {}, "events": [], "done": []}"""


class MemoryProvider(MemoryProviderBase):
    """档案 + 事件的结构化记忆。"""

    def __init__(self, config: Dict[str, Any], summary_memory: Optional[str] = None):
        super().__init__(config)
        self.config = config or {}
        self.memory_path = self.config.get("memory_path") or (
            get_project_dir() + DEFAULT_MEMORY_PATH
        )
        self.max_profile_chars = int(self.config.get("max_profile_chars", 800))
        self.max_events = int(self.config.get("max_events", 200))
        self.ttl_days = int(self.config.get("ttl_days", 365))
        self.retrieve_top_k = int(self.config.get("retrieve_top_k", 5))
        self.inject_max_chars = int(self.config.get("inject_max_chars", 1000))
        self.min_user_turns = int(self.config.get("min_user_turns", 2))
        self.min_user_chars = int(self.config.get("min_user_chars", 8))
        self.compact_threshold = int(self.config.get("compact_threshold", 150))
        self.enabled = bool(self.config.get("enabled", True))
        self.llm = None
        self.role_id = None
        self._data: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------ 生命周期

    def init_memory(self, role_id, llm, summary_memory=None, save_to_file=True, **kwargs):
        super().init_memory(role_id, llm, **kwargs)
        self.role_id = role_id
        self.llm = llm
        self._load()

    # ------------------------------------------------------------------ 存储

    def _bucket_key(self) -> str:
        return str(self.role_id or "default")

    def _load(self, force: bool = False) -> Dict[str, Any]:
        if self._data is not None and not force:
            return self._data
        raw = {}
        if os.path.exists(self.memory_path):
            try:
                with open(self.memory_path, "r", encoding="utf-8") as f:
                    raw = json.load(f) or {}
            except (json.JSONDecodeError, OSError) as e:
                # 损坏不阻塞对话：备份后重建
                backup = f"{self.memory_path}.corrupt-{int(time.time())}"
                try:
                    os.replace(self.memory_path, backup)
                except OSError:
                    pass
                logger.bind(tag=TAG).warning(f"记忆文件损坏，已备份到 {backup}: {e}")
                raw = {}
        if not isinstance(raw, dict):
            raw = {}
        raw.setdefault("version", 1)
        raw.setdefault("roles", {})
        self._data = raw
        return self._data

    def _bucket(self) -> Dict[str, Any]:
        data = self._load()
        bucket = data["roles"].setdefault(
            self._bucket_key(), {"profile": {}, "profile_updated": {}, "events": []}
        )
        bucket.setdefault("profile", {})
        bucket.setdefault("profile_updated", {})
        bucket.setdefault("events", [])
        return bucket

    def _save(self) -> None:
        if self._data is None:
            return
        os.makedirs(os.path.dirname(self.memory_path) or ".", exist_ok=True)
        # 重新读盘再写：只替换当前角色，保留其他角色（多实例/多设备安全）
        on_disk: Dict[str, Any] = {}
        if os.path.exists(self.memory_path):
            try:
                with open(self.memory_path, "r", encoding="utf-8") as f:
                    on_disk = json.load(f) or {}
            except (json.JSONDecodeError, OSError):
                on_disk = {}
        if not isinstance(on_disk, dict):
            on_disk = {}
        on_disk.setdefault("version", self._data.get("version", 1))
        on_disk.setdefault("roles", {})
        on_disk["roles"][self._bucket_key()] = self._data["roles"][self._bucket_key()]
        tmp = f"{self.memory_path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(on_disk, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.memory_path)

    # ------------------------------------------------------------------ 写入

    def _is_trivial(self, msgs) -> bool:
        """门槛：单轮问答 + 用户话很短 → 不值得抽取。

        多轮对话里的「对」这类确认不会被拦（助手轮数 > 1）。
        阈值走 min_user_chars（默认 8），只拦「嗯」「好的」这种。
        """
        user_msgs = [m for m in msgs if getattr(m, "role", "") == "user"]
        assistant_turns = sum(1 for m in msgs if getattr(m, "role", "") == "assistant")
        user_chars = sum(len(getattr(m, "content", "") or "") for m in user_msgs)
        if not user_msgs:
            return True
        if len(user_msgs) == 1 and assistant_turns <= 1 and user_chars < self.min_user_chars:
            return True
        return False

    @staticmethod
    def _session_text(msgs, limit: int = 3000) -> str:
        lines = []
        for m in msgs:
            role = getattr(m, "role", "")
            if role not in ("user", "assistant"):
                continue
            content = (getattr(m, "content", "") or "").strip()
            if not content:
                continue
            label = "用户" if role == "user" else "助手"
            lines.append(f"{label}：{content[:400]}")
        text = "\n".join(lines)
        if len(text) <= limit:
            return text
        # 长会话保留「开头 + 结尾」：自我陈述通常在开头，结论在结尾
        head, tail = limit * 2 // 3, limit // 3
        return text[:head] + "\n……（中间省略）……\n" + text[-tail:]

    async def save_memory(self, msgs, session_id=None):
        if not self.enabled or not msgs:
            return None
        if self.llm is None:
            logger.bind(tag=TAG).warning("记忆抽取跳过：LLM 未初始化")
            return None
        if self._is_trivial(msgs):
            logger.bind(tag=TAG).info("记忆抽取跳过：单轮短会话")
            return None

        self._load(force=True)  # 多实例场景下先同步磁盘最新状态
        bucket = self._bucket()
        before = json.dumps(bucket, ensure_ascii=False, sort_keys=True)
        try:
            prompt = self._build_extract_prompt(bucket, msgs)
            started = time.time()
            raw = self.llm.response_no_stream(
                EXTRACT_PROMPT, prompt, max_tokens=600, temperature=0.1
            )
            elapsed_ms = int((time.time() - started) * 1000)
            delta = self._parse_json(raw)
            if delta is None:
                logger.bind(tag=TAG).warning("记忆抽取返回无法解析，保持原样")
                return None
            self._merge(bucket, delta)
            self._enforce_limits(bucket)
            self._save()
            logger.bind(tag=TAG).info(
                f"记忆已更新：profile={len(bucket['profile'])} 字段，"
                f"events={len(bucket['events'])} 条，抽取耗时 {elapsed_ms}ms"
            )
            return bucket
        except Exception as e:  # noqa: BLE001
            # 失败回滚：内存和文件都不动
            if self._data is not None:
                self._data["roles"][self._bucket_key()] = json.loads(before)
            logger.bind(tag=TAG).error(f"记忆抽取失败，已回滚: {e}")
            return None

    def _build_extract_prompt(self, bucket: Dict[str, Any], msgs) -> str:
        profile = json.dumps(bucket.get("profile", {}), ensure_ascii=False)
        recent = bucket.get("events", [])[-20:]
        events_text = "\n".join(
            f"- {e.get('date', '')} {e.get('text', '')} [{','.join(e.get('tags', []))}]"
            + (f" due={e['due']}" if e.get("due") else "")
            for e in recent
        ) or "（无）"
        session = self._session_text(msgs)
        return (
            f"今天是 {time.strftime('%Y-%m-%d')}。\n\n"
            f"当前档案：{profile}\n\n"
            f"最近事件：\n{events_text}\n\n"
            f"本次对话：\n{session}\n\n"
            f"按规则输出 JSON。"
        )

    @staticmethod
    def _parse_json(raw: str) -> Optional[Dict[str, Any]]:
        if not raw:
            return None
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        data.setdefault("profile", {})
        data.setdefault("events", [])
        data.setdefault("done", [])
        if not isinstance(data["profile"], dict):
            data["profile"] = {}
        if not isinstance(data["events"], list):
            data["events"] = []
        if not isinstance(data["done"], list):
            data["done"] = []
        return data

    def _merge(self, bucket: Dict[str, Any], delta: Dict[str, Any]) -> None:
        today = time.strftime("%Y-%m-%d")
        extra_events: List[Dict[str, Any]] = []

        # 档案：覆盖式更新（supersession），键先归一化再按白名单过滤。
        # 白名单外的键不丢：转成事件（自由文本、可检索），避免静默丢失信息。
        for key, value in delta.get("profile", {}).items():
            if not isinstance(key, str) or not key.strip():
                continue
            if value is None:
                continue
            value = self._clip(str(value), 50)
            if not value:
                continue
            raw_key = key.strip()[:10]
            canonical = PROFILE_KEY_ALIASES.get(raw_key, raw_key)
            if canonical not in PROFILE_KEYS:
                logger.bind(tag=TAG).info(f"档案键 {raw_key} 不在白名单，转为事件")
                extra_events.append(
                    {"text": f"{canonical}：{value}", "tags": [canonical]}
                )
                continue
            bucket["profile"][canonical] = value
            bucket["profile_updated"][canonical] = today

        # 完成标记：把匹配到的事件标为 done
        for kw in delta.get("done", []):
            if not isinstance(kw, str) or len(kw.strip()) < 2:
                continue
            for event in bucket["events"]:
                if event.get("status") == "open" and kw.strip()[:20] in event.get("text", ""):
                    event["status"] = "done"

        # 事件：去重后追加（含白名单转成的事件）
        existing = {
            (e.get("date", ""), self._norm(e.get("text", ""))) for e in bucket["events"]
        }
        for item in list(delta.get("events", [])) + extra_events:
            if isinstance(item, str):
                item = {"text": item}
            if not isinstance(item, dict):
                continue
            text = self._clip(str(item.get("text", "")), 60)
            if not text:
                continue
            key = (today, self._norm(text))
            if key in existing:
                continue
            tags = item.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            tags = [str(t).strip()[:10] for t in tags if str(t).strip()][:3]
            status = "done" if item.get("status") == "done" else "open"
            due = self._parse_due(item.get("due"))
            bucket["events"].append(
                {"date": today, "text": text, "tags": tags, "status": status, "due": due}
            )
            existing.add(key)

    @staticmethod
    def _norm(text: str) -> str:
        return re.sub(r"\s+", "", text).lower()

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        """超长截断并加省略号（硬切会让「儿子豆包上」这种断句很难看）。"""
        text = (text or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(limit - 1, 0)] + "…"

    @staticmethod
    def _parse_due(value: Any) -> str:
        """只接受 YYYY-MM-DD，其余一律当没有截止日。"""
        if not isinstance(value, str):
            return ""
        text = value.strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return ""
        try:
            time.strptime(text, "%Y-%m-%d")
        except ValueError:
            return ""
        return text

    @staticmethod
    def _is_expired(event: Dict[str, Any]) -> bool:
        """有截止日且已过去、又没被标记完成 → 视为过期（仍然注入，但打标）。"""
        due = event.get("due", "")
        if not due or event.get("status") == "done":
            return False
        return due < time.strftime("%Y-%m-%d")

    def _enforce_limits(self, bucket: Dict[str, Any]) -> None:
        # 档案总长度上限：按更新时间丢最旧字段
        while len(json.dumps(bucket["profile"], ensure_ascii=False)) > self.max_profile_chars:
            updated = bucket.get("profile_updated", {})
            if not bucket["profile"]:
                break
            oldest = min(bucket["profile"], key=lambda k: updated.get(k, ""))
            bucket["profile"].pop(oldest, None)
            updated.pop(oldest, None)

        # TTL
        if self.ttl_days > 0:
            cutoff = time.strftime(
                "%Y-%m-%d", time.localtime(time.time() - self.ttl_days * 86400)
            )
            bucket["events"] = [
                e for e in bucket["events"] if e.get("date", "") >= cutoff
            ]

        # 压缩（先压缩，再按条数截断；否则旧事件先被截掉，压缩永远不触发）
        if self.compact_threshold and len(bucket["events"]) > self.compact_threshold:
            try:
                self._compact(bucket)
            except Exception as e:  # noqa: BLE001
                logger.bind(tag=TAG).warning(f"记忆压缩失败，退化为截断: {e}")

        # 条数上限：丢最旧
        if len(bucket["events"]) > self.max_events:
            bucket["events"] = bucket["events"][-self.max_events :]

    def _compact(self, bucket: Dict[str, Any]) -> None:
        """把较旧的事件压成摘要事件（一次 LLM 调用）。"""
        keep = max(self.max_events // 2, 5)
        keep = min(keep, max(len(bucket["events"]) - 1, 0))
        if keep <= 0:
            return
        old = bucket["events"][:-keep]
        recent = bucket["events"][-keep:]
        if not old:
            return
        text = "\n".join(f"- {e['date']} {e['text']}" for e in old)
        prompt = (
            f"把下面这些旧事件压缩成不超过 10 条摘要，每条 ≤40 字，保留日期范围和关键结论。"
            f"只输出 JSON：{{\"events\": [{{\"date\": \"YYYY-MM-DD\", \"text\": \"...\", \"tags\": []}}]}}\n\n{text}"
        )
        raw = self.llm.response_no_stream(
            "你是记忆压缩器，只输出 JSON。", prompt, max_tokens=800, temperature=0.1
        )
        data = self._parse_json(raw)
        merged = []
        for item in (data or {}).get("events", []):
            if not isinstance(item, dict):
                continue
            t = str(item.get("text", "")).strip()[:60]
            if not t:
                continue
            merged.append(
                {
                    "date": str(item.get("date", old[0].get("date", "")))[:10],
                    "text": t,
                    "tags": [str(x)[:10] for x in (item.get("tags") or [])][:3],
                    "status": "done",
                    # 摘要事件是多条聚合，没有单一时点，due 一律清空
                    "due": "",
                }
            )
        if merged:
            bucket["events"] = merged + recent

    # ------------------------------------------------------------------ 读取

    @staticmethod
    def _keywords(query: str) -> List[str]:
        """中文无分词：用 2-4 字 n-gram + 停用词过滤。"""
        stop = {
            "上次", "那个", "这个", "的事", "事情", "怎么", "什么", "为什", "你还",
            "记得", "之前", "后来", "现在", "我们", "我的", "你的", "一下", "帮我",
            "告诉", "说的", "聊的", "那个事", "怎么样", "还记得",
        }
        clean = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", " ", query or "")
        grams = set()
        for chunk in clean.split():
            for n in (2, 3, 4):
                for i in range(len(chunk) - n + 1):
                    g = chunk[i : i + n]
                    if g in stop:
                        continue
                    grams.add(g)
        return list(grams)

    @staticmethod
    def _is_meta_query(query: str) -> bool:
        """「你记得什么 / 复述一下记忆」这类元问题不含事件关键词，单独放行最近事件。"""
        return any(word in (query or "") for word in META_QUERY_WORDS)

    def _recent_events(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        k = max(self.retrieve_top_k, 1)
        alive = [e for e in events if e.get("status") != "done"] or list(events)
        return list(reversed(alive[-k:]))

    def _match_events(self, query: str) -> List[Dict[str, Any]]:
        bucket = self._bucket()
        events = bucket.get("events", [])
        if not events:
            return []
        if self._is_meta_query(query):
            return self._recent_events(events)
        grams = self._keywords(query)
        if not grams:
            return []
        scored = []
        for idx, event in enumerate(events):
            text = event.get("text", "")
            tags = event.get("tags", []) or []
            score = 0
            for g in grams:
                if any(g in str(t) for t in tags):
                    score += 3
                elif g in text:
                    score += 1
            if score > 0:
                scored.append((score, idx, event))
        # 先按命中强度，再按时间（越新越前）
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [e for _, _, e in scored[: self.retrieve_top_k]]

    async def query_memory(self, query: str) -> str:
        if not self.enabled:
            return ""
        self._load(force=True)  # 每轮读盘，保证多实例一致（文件很小，<1ms）
        bucket = self._bucket()
        lines = []
        for key, value in bucket.get("profile", {}).items():
            lines.append(f"- {key}：{value}")
        for event in self._match_events(query):
            if event.get("status") == "done":
                mark = "（已完成）"
            elif self._is_expired(event):
                mark = "（已过期）"
            else:
                mark = ""
            lines.append(f"- {event.get('date', '')}：{event.get('text', '')}{mark}")
        text = "\n".join(lines).strip()
        if not text:
            return ""
        if len(text) > self.inject_max_chars:
            text = text[: self.inject_max_chars].rsplit("\n", 1)[0]
        return text

    # ------------------------------------------------------------------ 调试

    def snapshot(self) -> Dict[str, Any]:
        """测试/排查用：从磁盘重新读取当前角色的完整记忆。"""
        self._load(force=True)
        return json.loads(json.dumps(self._bucket(), ensure_ascii=False))
