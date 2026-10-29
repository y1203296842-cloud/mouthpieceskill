"""adapters/markdown_provider.py — 默认 Markdown Provider。

数据源（人工可读、Agent 可读、脚本可解析）：
- knowledge/cases/*.md                    案例（可复用的话术知识，含 scope/intent/边界）
- knowledge/strategies/*.md               策略（为什么这样说）
- knowledge/index/case_manifest.json      自动生成的轻量检索索引（检索层只用它召回候选）
- memory/user|people|events|feedback|candidates/*.md
- raw/                                    （可选）数据溯源层：博主名/地址/视频链接，不进检索

检索原则：**先定位，再读取**。
  用户提问 → 在轻量索引(case_manifest)上打分召回 Top-K → 只加载候选 case 全文
  → 场景适配判定（语义+意图+场景/关系适配+scope 惩罚）→ 输出。
  不再把整个知识库全量塞给模型。
未来替换实现（SQLite/向量库/Mem0）时，只需实现 adapters/base.py 接口，Core 不重写。
"""
from __future__ import annotations

import json
import os
import re
import glob
import time
from typing import Any, Dict, List, Optional, Set

from .base import (
    Case, CaseHit, ContextProvider, DOMAIN_MARKERS, FeedbackProvider,
    GENERIC_KEYS, GenerationPackage, KnowledgeProvider, MemoryProvider,
    QueryAnalysis, RELATION_ALIASES, Retriever, RetrieveBundle, RetrievalService,
    RuntimeState, Strategy, StyleProvider, StyleSpec, UserStyle, dict_to_style,
)


def project_root() -> str:
    """定位项目根（含 knowledge/ 与 memory/ 的目录）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)          # adapters/ -> 项目根
    if os.path.isdir(os.path.join(root, "knowledge")):
        return root
    cur = here
    for _ in range(4):
        cur = os.path.dirname(cur)
        if os.path.isdir(os.path.join(cur, "knowledge")):
            return cur
    return root


ROOT = project_root()


# ---------------------------------------------------------------------------
# 简易 front-matter 解析
# ---------------------------------------------------------------------------
def parse_front_matter(text: str) -> Dict[str, Any]:
    """解析 `---` 包裹的 `key: value` / `key: [a, b]` 块。"""
    fm: Dict[str, Any] = {}
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return fm
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if val.startswith("[") and val.endswith("]"):
            items = [x.strip().strip("'\"") for x in val[1:-1].split(",")]
            fm[key] = [x for x in items if x and x != "-"]   # "-" 占位 = 空约束
        else:
            fm[key] = val
    return fm


def _first_list_block(text: str, header: str) -> List[str]:
    m = re.search(rf"^##\s*{re.escape(header)}\s*$.*?(?=^##|\Z)", text, re.S | re.M)
    if not m:
        return []
    return [line[2:].strip() for line in m.group(0).splitlines() if line.strip().startswith("- ")]


def _text_block(text: str, header: str) -> str:
    m = re.search(rf"^##\s*{re.escape(header)}\s*$.*?(?=^##|\Z)", text, re.S | re.M)
    return m.group(0).strip() if m else ""


# ---------------------------------------------------------------------------
# 轻量检索工具
# ---------------------------------------------------------------------------
def _tokenize(text: str) -> List[str]:
    text = text.lower()
    return [t for t in re.split(r"[\s,，。！？!?（）()【】\[\]'\"“”、\-—_]+", text) if t]


def _bigrams(text: str) -> Set[str]:
    text = text.replace(" ", "")
    return {text[i:i + 2] for i in range(len(text) - 1) if len(text[i:i + 2]) == 2}


def _norm(s: str) -> str:
    return re.sub(r"[\s，。、,.!！?？:：的了吗呢]", "", s)


# 无区分度双字组：人物/事件扫描时忽略，避免"怎么回/处理"这类通用词造成误召回
STOP_BIGRAMS = {
    "怎么", "么回", "回事", "处理", "方式", "办法", "帮助", "回答", "东西",
    "事情", "一个", "这个", "那个", "什么", "样子", "感觉", "就是", "问题",
    "时候", "可以", "还是", "但是", "比如", "回复", "刚才",
}


def _detect_relation(text: str) -> str:
    for rel, kws in RELATION_ALIASES.items():
        if any(k and k in text for k in kws):
            return rel
    return ""


def _detect_domains(case) -> Set[str]:
    """根据 case 内容与领域标记，推断它主要绑定哪个领域（用于跨领域惩罚）。"""
    hay = case.canonical_trigger + case.intent + "".join(case.response) + "".join(case.scene_constraints)
    out = set()
    for dom, markers in DOMAIN_MARKERS.items():
        if any(m and m in hay for m in markers):
            out.add(dom)
    return out


# ---------------------------------------------------------------------------
# MarkdownKnowledgeProvider
# ---------------------------------------------------------------------------
class MarkdownKnowledgeProvider(KnowledgeProvider):
    def __init__(self, root: str = ROOT) -> None:
        self.root = root
        self.cases_dir = os.path.join(root, "knowledge", "cases")
        self.strategies_dir = os.path.join(root, "knowledge", "strategies")
        self.index_dir = os.path.join(root, "knowledge", "index")

    # ---- cases 读写 ----
    def list_cases(self) -> List[Case]:
        out = []
        for path in sorted(glob.glob(os.path.join(self.cases_dir, "*.md"))):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            c = self._case_from_md(path, text)
            if c:
                out.append(c)
        return out

    def get_case(self, case_id: str) -> Optional[Case]:
        path = os.path.join(self.cases_dir, f"{case_id}.md")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return self._case_from_md(path, f.read())
        for c in self.list_cases():
            if c.case_id == case_id:
                return c
        return None

    @staticmethod
    def _case_from_md(path: str, text: str) -> Optional[Case]:
        fm = parse_front_matter(text)
        cid = str(fm.get("case_id", fm.get("id", "")))
        if not cid:
            return None
        response = _first_list_block(text, "response") or _first_list_block(text, "replies")
        query_examples = _first_list_block(text, "user_query_examples")
        strategy_refs = [str(x) for x in fm.get("strategy_refs", [])]
        if not strategy_refs:
            strategy_refs = _first_list_block(text, "strategy_refs")
        return Case(
            case_id=cid,
            canonical_trigger=str(fm.get("canonical_trigger", fm.get("trigger", ""))),
            intent=str(fm.get("intent", "")),
            response=response,
            scope=str(fm.get("scope", "broad")),
            scene_constraints=[str(x) for x in fm.get("scene_constraints", [])],
            relation_constraints=[str(x) for x in fm.get("relation_constraints", [])],
            keywords=[str(x) for x in fm.get("keywords", [])],
            status=str(fm.get("status", "verified")),
            query_examples=query_examples,
            usage=str(fm.get("usage", "")) or _text_block(text, "usage"),
            tone=str(fm.get("tone", "")),
            strength=str(fm.get("strength", "")),
            strategy_refs=strategy_refs,
        )

    def upsert_case(self, case: Case) -> None:
        def fmt_list(xs: List[str]) -> str:
            return ", ".join(xs) if xs else "-"
        fm = (
            f"---\ncase_id: {case.case_id}\ncanonical_trigger: {case.canonical_trigger}\n"
            f"intent: {case.intent}\nscope: {case.scope}\n"
            f"scene_constraints: [{fmt_list(case.scene_constraints)}]\n"
            f"relation_constraints: [{fmt_list(case.relation_constraints)}]\n"
            f"keywords: [{fmt_list(case.keywords)}]\nstatus: {case.status}\n"
            f"tone: {case.tone}\nstrength: {case.strength}\n"
            f"strategy_refs: [{fmt_list(case.strategy_refs)}]\n---\n\n"
        )
        body = "## user_query_examples\n"
        for q in case.query_examples:
            body += f"- {q}\n"
        body += "\n## response\n"
        for r in case.response:
            body += f"- {r}\n"
        if case.usage:
            body += f"\n## usage\n{case.usage}\n"
        os.makedirs(self.cases_dir, exist_ok=True)
        path = os.path.join(self.cases_dir, f"{case.case_id}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(fm + body)

    # ---- strategies ----
    def list_strategies(self) -> List[Strategy]:
        out = []
        for path in sorted(glob.glob(os.path.join(self.strategies_dir, "*.md"))):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            s = self._strategy_from_md(text)
            if s:
                out.append(s)
        return out

    def get_strategy(self, strategy_id: str) -> Optional[Strategy]:
        path = os.path.join(self.strategies_dir, f"{strategy_id}.md")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return self._strategy_from_md(f.read())
        for s in self.list_strategies():
            if s.id == strategy_id:
                return s
        return None

    @staticmethod
    def _strategy_from_md(text: str) -> Optional[Strategy]:
        fm = parse_front_matter(text)
        if "id" not in fm:
            return None
        return Strategy(
            id=str(fm.get("id")),
            name=str(fm.get("name", "")),
            description=str(fm.get("description", "")) or _text_block(text, "description"),
            how_to_use=str(fm.get("how_to_use", "")),
            applicable_scenes=[str(x) for x in fm.get("applicable_scenes", [])],
            avoid_when=str(fm.get("avoid_when", "")),
            confidence=str(fm.get("confidence", "candidate")),
            examples=[str(x) for x in fm.get("examples", [])],
        )

    def upsert_strategy(self, strategy: Strategy) -> None:
        scenes = ", ".join(strategy.applicable_scenes)
        ex = ", ".join(strategy.examples)
        fm = (
            f"---\nid: {strategy.id}\nname: {strategy.name}\nconfidence: {strategy.confidence}\n"
            f"applicable_scenes: [{scenes}]\nexamples: [{ex}]\n---\n\n"
        )
        body = (
            f"## description\n{strategy.description}\n\n"
            f"## how_to_use\n{strategy.how_to_use}\n\n"
            f"## avoid_when\n{strategy.avoid_when}\n"
        )
        os.makedirs(self.strategies_dir, exist_ok=True)
        path = os.path.join(self.strategies_dir, f"{strategy.id}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(fm + body)

    # ---- 检索（索引召回 → 场景适配） ----
    def _manifest(self) -> List[Dict[str, Any]]:
        """读取自动索引；缺失时即时构建（兜底，不阻塞检索）。"""
        mp = os.path.join(self.index_dir, "case_manifest.json")
        if os.path.exists(mp):
            try:
                with open(mp, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return self._build_manifest_from_cases()

    def _build_manifest_from_cases(self) -> List[Dict[str, Any]]:
        out = []
        for c in self.list_cases():
            out.append(self._entry_for(c))
        return out

    @staticmethod
    def _entry_for(c: Case) -> Dict[str, Any]:
        return {
            "case_id": c.case_id,
            "canonical_trigger": c.canonical_trigger,
            "intent": c.intent,
            "query_examples": c.query_examples,
            "keywords": c.keywords,
            "scope": c.scope,
            "scene_constraints": c.scene_constraints,
            "relation_constraints": c.relation_constraints,
            "status": c.status,
            "tone": c.tone,
            "strength": c.strength,
        }

    def search_case_hits(self, text: str, top_k: int = 6) -> List[CaseHit]:
        """在轻量索引上打分召回 Top-K，只加载候选 case 全文。"""
        # 只对 verified 案例开放检索；needs_review / rejected 不进入正式检索
        entries = [e for e in self._manifest() if e.get("status", "verified") == "verified"]
        scored = []
        for e in entries:
            s = _score_manifest(e, text)
            if s["semantic"] > 0 or s["intent"] > 0 or s["relation_fit"] > 0:
                scored.append((s["total"], e))
        scored.sort(key=lambda x: -x[0])
        hits = []
        for _, e in scored[:top_k]:
            case = self.get_case(e["case_id"])
            if case:
                s = _score_manifest(e, text)
                hits.append(CaseHit(
                    case=case, semantic=s["semantic"], intent=s["intent"],
                    scene_fit=s["scene_fit"], relation_fit=s["relation_fit"],
                    scope_penalty=s["scope_penalty"], total=s["total"],
                    path=os.path.join(self.cases_dir, f"{case.case_id}.md"),
                ))
        return hits

    def search_cases(self, text: str, top_k: int = 6) -> List[Case]:
        return [h.case for h in self.search_case_hits(text, top_k)]

    def search_strategies(self, text: str, top_k: int = 4) -> List[Strategy]:
        scored = []
        for s in self.list_strategies():
            score = _score_strategy(s, text)
            if score > 0:
                scored.append((score, s))
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:top_k]]


def _score_manifest(e: Dict[str, Any], text: str) -> Dict[str, float]:
    """场景适配评分：语义 + 意图 + 场景/关系适配 + scope 惩罚。"""
    trig = e.get("canonical_trigger", "")
    intent = e.get("intent", "")
    qex = " ".join(e.get("query_examples", []))
    keywords = e.get("keywords", [])
    scope = e.get("scope", "broad")
    rel_constraints = e.get("relation_constraints", [])
    scene_constraints = e.get("scene_constraints", [])

    semantic = 0.0
    for kw in keywords:
        if kw and kw not in GENERIC_KEYS and kw in text:
            semantic += 3.0
    if trig and (trig in text or text in trig):
        semantic += 5.0
    for q in e.get("query_examples", []):
        if q and (q in text or text in q):
            semantic += 2.5
        elif q:
            semantic += len(_bigrams(text) & _bigrams(q)) * 0.4
    semantic += len(_bigrams(text) & _bigrams(trig + intent)) * 0.3

    # 意图匹配：intent 里出现用户问题中的意图词
    intent_score = 0.0
    if intent:
        itokens = _tokenize(intent)
        if any(t and t in text for t in itokens):
            intent_score += 3.0
        intent_score += len(_bigrams(text) & _bigrams(intent)) * 0.5

    # 关系适配
    user_rel = _detect_relation(text)
    relation_fit = 0.0
    if rel_constraints:
        if user_rel and any(r in rel_constraints for r in RELATION_ALIASES.get(user_rel, [user_rel])):
            relation_fit += 3.0
        elif user_rel:
            relation_fit -= 3.0   # 明确限定关系且用户关系不符 → 降权
    else:
        relation_fit += 1.0       # 无限制 → 兼容（通用）

    # 场景适配
    scene_fit = 0.0
    if scene_constraints:
        if any(sc and sc in text for sc in scene_constraints):
            scene_fit += 2.0
    else:
        scene_fit += 1.0

    # scope 惩罚：领域限定的 case 若用户处于冲突领域/关系，重罚
    scope_penalty = 0.0
    user_domains = {dom for dom, ms in DOMAIN_MARKERS.items() if any(m and m in text for m in ms)}
    if scope in ("domain_specific", "scene_specific"):
        # 用 manifest 里能看到的领域线索判断
        hay = trig + intent + "".join(keywords) + "".join(scene_constraints) + "".join(rel_constraints)
        case_domains = {dom for dom, ms in DOMAIN_MARKERS.items() if any(m and m in hay for m in ms)}
        if user_domains and case_domains and not (user_domains & case_domains):
            scope_penalty += 5.0
        elif scope == "domain_specific" and user_rel and not rel_constraints and user_rel in ("室友", "朋友"):
            # 领域限定但没写关系限制，用户在非对应领域 → 谨慎降权
            scope_penalty += 2.0

    total = semantic + intent_score + relation_fit + scene_fit - scope_penalty
    return {"semantic": semantic, "intent": intent_score, "scene_fit": scene_fit,
            "relation_fit": relation_fit, "scope_penalty": scope_penalty, "total": total}


def _score_strategy(s: Strategy, text: str) -> float:
    score = 0.0
    hay = s.name + s.description + "".join(s.applicable_scenes)
    if s.name and s.name in text:
        score += 3.0
    if s.description and s.description in text:
        score += 3.0
    for sc in s.applicable_scenes:
        if sc and sc in text:
            score += 1.5
    score += len(_bigrams(text) & _bigrams(hay)) * 0.4
    return score


# ---------------------------------------------------------------------------
# MarkdownMemoryProvider
# ---------------------------------------------------------------------------
class MarkdownMemoryProvider(MemoryProvider):
    """用户记忆按用户隔离：memory/users/<user_id>/ 下分 user/people/events/feedback/candidates/preferences。
    默认 user_id="default"。公共知识库（knowledge/）只读、全员共享；用户层数据互不覆盖。"""

    def __init__(self, root: str = ROOT, user_id: str = "default") -> None:
        self.root = root
        self.user_id = (user_id or "default").strip()
        base = os.path.join(root, "memory", "users", self.user_id)
        self.user_dir = os.path.join(base, "user")
        self.people_dir = os.path.join(base, "people")
        self.events_dir = os.path.join(base, "events")
        self.feedback_dir = os.path.join(base, "feedback")
        self.candidates_dir = os.path.join(base, "candidates")
        self.prefs_dir = os.path.join(base, "preferences")
        self._base = base

    def reset_prefs(self) -> bool:
        """清除该用户的风格偏好数据（画像/测评记录/风格笔记），恢复 Skill 内置默认能力。
        称呼与人物/事件记忆保留——重置的是'回答风格偏好'，不是用户关系记忆。"""
        removed = False
        for sub in ("preferences",):
            d = os.path.join(self._base, sub)
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    fp = os.path.join(d, fn)
                    if os.path.isfile(fp):
                        os.remove(fp)
                        removed = True
        return removed

    def get_user_style(self) -> UserStyle:
        path = os.path.join(self.user_dir, "style.md")
        if not os.path.exists(path):
            return UserStyle(name=self.get_name())
        with open(path, encoding="utf-8") as f:
            text = f.read()
        fm = parse_front_matter(text)
        notes = _first_list_block(text, "行为观察") or []
        return UserStyle(
            tone=str(fm.get("tone", "")),
            length=str(fm.get("length", "short")),
            strength=str(fm.get("strength", "medium")),
            no_ai_tone=bool(fm.get("no_ai_tone", True)),
            name=str(fm.get("name", "")) or self.get_name(),
            notes=notes,
        )

    def get_name(self) -> str:
        path = os.path.join(self.user_dir, "name.md")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as f:
            return f.read().strip()

    def set_name(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            return
        os.makedirs(self.user_dir, exist_ok=True)
        with open(os.path.join(self.user_dir, "name.md"), "w", encoding="utf-8") as f:
            f.write(name)

    def set_preference(self, note: str) -> None:
        path = os.path.join(self.user_dir, "preferences.md")
        _append_item(path, note, "## 偏好")

    def upsert_person(self, name: str, content: Dict[str, Any]) -> None:
        fm = f"---\nname: {name}\nrelation: {content.get('relation', '')}\nsince: {content.get('since', '')}\n---\n\n"
        body = "## 记忆\n" + "\n".join(f"- {k}: {v}" for k, v in content.items()) + "\n"
        path = os.path.join(self.people_dir, f"{name}.md")
        os.makedirs(self.people_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(fm + body)

    def upsert_event(self, key: str, content: Dict[str, Any]) -> None:
        fm = f"---\nkey: {key}\n---\n\n"
        body = "## 事件\n" + "\n".join(f"- {k}: {v}" for k, v in content.items()) + "\n"
        path = os.path.join(self.events_dir, f"{key}.md")
        os.makedirs(self.events_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(fm + body)

    def add_feedback(self, note: str) -> None:
        path = os.path.join(self.feedback_dir, "feedback.md")
        _append_item(path, note, "## 反馈")

    def add_candidate(self, note: str, category: str) -> None:
        path = os.path.join(self.candidates_dir, "candidates.md")
        _append_item(path, f"[{category}] {note}", "## 候选记忆")

    def promote(self, note: str, target: str) -> None:
        if target == "style":
            path = os.path.join(self.user_dir, "style.md")
            _append_item(path, note, "## 行为观察")
        elif target == "taboos":
            path = os.path.join(self.user_dir, "taboos.md")
            _append_item(path, note, "## 雷区")
        else:
            self.set_preference(note)


def _append_item(path: str, item: str, header: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"---\nmemory_version: 1\n---\n\n{header}\n")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if item in text:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- {item}\n")


# ---------------------------------------------------------------------------
# 组合：Retriever / Feedback / Style / Context
# ---------------------------------------------------------------------------
class MarkdownRetriever(Retriever):
    """按优先级返回 用户风格 → 人物 → 事件 → 反馈 → 案例 → 策略。"""

    def __init__(self, memory: Optional[MemoryProvider] = None,
                 knowledge: Optional[KnowledgeProvider] = None) -> None:
        self.memory = memory or MarkdownMemoryProvider()
        self.knowledge = knowledge or MarkdownKnowledgeProvider()

    def retrieve_user(self, user_input: str, top_k: int = 6) -> RetrieveBundle:
        b = RetrieveBundle(style=self.memory.get_user_style())
        b.people = self._scan_dir(self.memory.people_dir, user_input)
        b.events = self._scan_dir(self.memory.events_dir, user_input)
        b.feedback = self._scan_feedback(user_input)
        b.cases = self.knowledge.search_cases(user_input, top_k=top_k)
        b.case_hits = self.knowledge.search_case_hits(user_input, top_k=top_k)
        b.strategies = self.knowledge.search_strategies(user_input, top_k=4)
        return b

    @staticmethod
    def _scan_dir(directory: str, text: str) -> List[str]:
        out = []
        if not os.path.isdir(directory):
            return out
        for path in sorted(glob.glob(os.path.join(directory, "*.md"))):
            with open(path, encoding="utf-8") as f:
                content = f.read()
            name = os.path.splitext(os.path.basename(path))[0]
            if name in text:
                out.append(name)
                continue
            shared = _bigrams(text) & _bigrams(content)
            shared = {b for b in shared if b not in STOP_BIGRAMS}
            if len(shared) >= 2:
                out.append(name)
        return out

    def _scan_feedback(self, text: str) -> List[str]:
        out = []
        path = os.path.join(self.memory.feedback_dir, "feedback.md")
        if not os.path.exists(path):
            return out
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("- "):
                    body = line[2:]
                    if any(t and t in text for t in _tokenize(body)) or _bigrams(text) & _bigrams(body):
                        out.append(body)
        return out


class MarkdownFeedbackProvider(FeedbackProvider):
    def __init__(self, memory: Optional[MemoryProvider] = None) -> None:
        self.memory = memory or MarkdownMemoryProvider()

    def record(self, user_input: str, ai_output: str, user_action: str) -> None:
        note = f"{user_action} | 场景:{user_input} | 输出:{ai_output[:40]}"
        self.memory.add_feedback(note)


class MemoryStyleProvider(StyleProvider):
    def __init__(self, memory: Optional[MemoryProvider] = None) -> None:
        self.memory = memory or MarkdownMemoryProvider()

    def current_style(self) -> UserStyle:
        return self.memory.get_user_style()


class DefaultContextProvider(ContextProvider):
    def __init__(self) -> None:
        self._ctx: List[str] = []

    def recent_context(self) -> List[str]:
        return list(self._ctx)

    def push(self, line: str) -> None:
        self._ctx.append(line)
        if len(self._ctx) > 20:
            self._ctx = self._ctx[-20:]


# ---------------------------------------------------------------------------
# 统一知识检索适配器（Agent/核心只认识这一个入口；一次调用 = 检索 + 选择素材 + 计时）
# ---------------------------------------------------------------------------
ACCUSE_MARKERS = ("别人", "同事", "室友", "舍友", "朋友", "领导", "老板", "上司", "上级",
                  "对象", "男朋友", "女朋友", "亲戚", "家人", "长辈", "陌生人", "说我", "阴阳")
REPLY_INTENT_MARKERS = ("怎么回", "怎么怼", "怎么办", "回击", "反击", "挡回去", "怼", "回他", "回她", "接话", "怼回去")
STRENGTH_MARKERS = {"稳": "稳", "冷": "冷", "狠": "狠", "锋利": "狠", "凶": "狠"}
# 真正的"指责/挑衅"信号：带"说我X/骂我/阴阳我/嫌弃/内涵/排挤/针对/看不上/欺负"等动作，才算指责
_ACCUSE_ACTION = ("说我", "说你", "骂我", "阴阳", "嫌弃", "内涵", "排挤", "针对", "欺负",
                  "看不上", "瞧不起", "嘲笑", "使唤", "催", "说我", "说我太", "说我X")


def _analyze_query(text: str) -> QueryAnalysis:
    a = QueryAnalysis()
    a.relation = _detect_relation(text)
    a.domains = [dom for dom, ms in DOMAIN_MARKERS.items() if any(m and m in text for m in ms)]
    a.is_accusation = any(m and m in text for m in _ACCUSE_ACTION)
    a.wants_reply = any(m and m in text for m in REPLY_INTENT_MARKERS)
    for k, v in STRENGTH_MARKERS.items():
        if k in text:
            a.strength_asked = v
            break
    return a


def _style_spec_from_cases(cases: List[Case]) -> StyleSpec:
    """从已入库案例自动归纳"这批话术为什么好"的整体风格（无匹配时也用这个生成，而非 AI 腔）。"""
    spec = StyleSpec()
    spec.source_case_count = len(cases)
    tone_cnt: Dict[str, int] = {}
    resp_len = 0
    n = 0
    short_cnt = 0
    for c in cases:
        if c.tone:
            tone_cnt[c.tone] = tone_cnt.get(c.tone, 0) + 1
        for r in c.response:
            resp_len += len(r)
            n += 1
            if len(r) <= 16:
                short_cnt += 1
            spec.sample.append(r)
    spec.tone = max(tone_cnt, key=tone_cnt.get) if tone_cnt else "冷"
    if n:
        spec.length = "short" if (short_cnt / n) >= 0.5 else "medium"
    spec.aggression = "medium"
    spec.structures = ["不解释", "反打回", "反问", "短句", "不接对方前提"]
    spec.forbid = ["我理解你的感受", "你可以尝试", "从沟通角度来看", "其实", "建议你",
                   "希望我的回答能够帮助到你", "心平气和地沟通", "尊重对方的感受"]
    spec.sample = spec.sample[:6]
    return spec


class MarkdownRetrievalService(RetrievalService):
    """默认检索服务：索引召回答案 → 召回回应思路(pattern) → 归纳风格 → 带计时打包。"""

    def __init__(self, memory: Optional[MemoryProvider] = None,
                 knowledge: Optional[KnowledgeProvider] = None) -> None:
        self.memory = memory or MarkdownMemoryProvider()
        self.knowledge = knowledge or MarkdownKnowledgeProvider()

    def available(self) -> bool:
        return os.path.isdir(self.knowledge.cases_dir) and bool(self.knowledge.list_cases())

    def set_name(self, name: str) -> None:
        self.memory.set_name(name)

    def get_name(self) -> str:
        return self.memory.get_name()

    def reset_prefs(self) -> bool:
        """代理到当前用户记忆：清除偏好画像，回落默认能力。"""
        return self.memory.reset_prefs()

    def remember_style(self, note: str) -> None:
        """把用户长期风格偏好写入该用户风格记忆（可追溯、可清除）。"""
        self.memory.promote(note, "style")

    def search(self, query: str, top_k: int = 6) -> List[CaseHit]:
        return self.knowledge.search_case_hits(query, top_k=top_k)

    def get_patterns(self, query: str, top_k: int = 4) -> List[Strategy]:
        return self.knowledge.search_strategies(query, top_k=top_k)

    def build_package(self, user_input: str, top_k: int = 6) -> GenerationPackage:
        t0 = time.perf_counter()
        state = RuntimeState(KB_AVAILABLE=self.available())
        pkg = GenerationPackage(query=user_input)
        pkg.state = state

        # T1 问题标准化
        pkg.analysis = _analyze_query(user_input)

        # T2 强制知识检索（gatekeeper：知识库可用 → 必须检索）
        state.KB_CALLED = state.KB_AVAILABLE
        t1 = time.perf_counter()
        if state.KB_AVAILABLE:
            pkg.answer_candidates = self.knowledge.search_case_hits(user_input, top_k=top_k)
            pkg.patterns = self.knowledge.search_strategies(user_input, top_k=4)
        t2 = time.perf_counter()
        state.KB_SEARCH_MS = round((t2 - t1) * 1000, 1)
        state.CANDIDATES = len(pkg.answer_candidates)
        # 语感参考：取最相近候选的 response 原文（无论匹配级别，仅供生成借鉴语言质感，不直引）
        seen = set()
        for h in pkg.answer_candidates[:4]:
            for r in (h.case.response or []):
                if r and r not in seen:
                    seen.add(r)
                    pkg.language_refs.append(r)
        pkg.language_refs = pkg.language_refs[:4]

        # T3 记忆 + 风格 + 全局风格规范
        pkg.style = self.memory.get_user_style()
        ret = MarkdownRetriever(self.memory, self.knowledge)
        rb = ret.retrieve_user(user_input, top_k=top_k)
        pkg.people, pkg.events, pkg.feedback = rb.people, rb.events, rb.feedback
        pkg.style_spec = _style_spec_from_cases(self.knowledge.list_cases())
        # 长期回怼经验（默认能力第二层）：随包提供给生成层参照，保证"学到的东西"参与生成而非仅存文档
        lessons_path = os.path.join(ROOT, "knowledge", "style", "lessons.md")
        if os.path.exists(lessons_path):
            with open(lessons_path, encoding="utf-8") as _f:
                _t = _f.read()
            pkg.lessons = [ln.strip()[2:] for ln in _t.splitlines()
                           if ln.strip().startswith("- ") and len(ln.strip()) > 4][:24]
        state.PATTERN_MATCH = len(pkg.patterns)
        state.TOTAL_MS = round((time.perf_counter() - t0) * 1000, 1)
        return pkg
