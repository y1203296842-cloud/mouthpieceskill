"""scripts/cleaner.py — 知识清洗器（脏数据 → 可复用话术知识）。

把抖音/小红书等原始文案（逐字稿、口播、带引导语/哈哈哈/标签）清洗成知识案例。
纯确定性规则（无外部依赖），便于复用与测试。

清洗 = **语义校验**，不是文本整理：
- ASR 纠错：识别错别字/同音字/口音误识（标题往往比逐字稿准）。
- trigger 恢复：综合 标题 + 逐字稿前置句 + 回复 + 常识，补全 canonical_trigger。
- scope 分类：broad / relational / scene_specific / domain_specific（区分"谁说的"与"说了什么"）。
- 失配机制：trigger ↔ response 对不上且无法恢复 → status=needs_review，不进正式库。
- **response 保留原始表达风格**（攻击性/幽默/阴阳/反问/冷淡感），不做规范化改写。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from adapters.base import DOMAIN_MARKERS, RELATION_ALIASES, RELATION_WORDS

# 噪音词：进不了知识库正文（可计为丢弃）
NOISE_RE = re.compile(
    r"(哈哈哈|嘿嘿|嘻嘻|哈哈|点赞|关注|收藏|转发|姐妹们|家人们|记住了吗|"
    r"双击屏幕|上热门|记得三连|欢迎关注|评论区|私信我|宝子们|"
    r"转场|下期再见|喜欢记得|点个赞|真的吗|你学会了吗|划重点|上干货)"
)

# 问题框架词：触发语之后、回复之前的提问框架（结构噪音，不进入正文）
QUESTION_FRAME_RE = re.compile(
    r"(要怎么|怎么才能|该怎么|怎么办|如何|怎么回|怎么怼|如何应对|该怎么回复|咋回|怎么整)"
)

# 高置信 ASR 纠错表（整词替换，保留表达风格；只做明确的同音/口音修正）
ASR_FIXES: Dict[str, str] = {
    "敏gan": "敏感", "敏gàn": "敏感", "敏干": "敏感", "太敏gan": "太敏感",
    "关系护": "关系户", "后门": "后门", "卡着点": "卡点",
    "招笑呗": "招笑呗", "像画儿": "像画儿",
}

# 具体角色 → 泛化角色（用于 broad 案例：把"同事/领导/室友…"归一为"别人/对象"）
ACTOR_GENERIC: Dict[str, str] = {
    "同事": "别人", "领导": "上级", "老板": "上级", "上司": "上级", "manager": "上级",
    "室友": "别人", "舍友": "别人", "朋友": "别人", "哥们": "别人", "闺蜜": "别人",
    "男朋友": "对象", "女朋友": "对象", "对象": "对象", "crush": "喜欢的人",
}

# broad 案例的跨关系问法覆盖
CROSS_RELATIONS = ["别人", "同事", "室友", "朋友", "家人"]

# 平级关系（同事/室友/朋友）：针对这类关系的"指责/阴阳"通常跨关系通用（太敏感、靠关系、不合群…）
# → 若 response 无强领域标记，判 broad。领导/对象/亲戚等权力或亲密关系仍判 relational，避免错误泛化。
GENERIC_PEER = {"同事", "室友", "朋友"}


@dataclass
class TypedReply:
    type_label: str
    text: str


@dataclass
class CleanedCase:
    """清洗产物：一个可入库的案例候选（新 schema 字段）。"""
    canonical_trigger: str = ""
    intent: str = ""
    response: List[str] = field(default_factory=list)
    scope: str = "broad"
    scene_constraints: List[str] = field(default_factory=list)
    relation_constraints: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    status: str = "verified"                 # verified / needs_review
    needs_review_reason: str = ""
    query_examples: List[str] = field(default_factory=list)
    usage: str = ""
    strategy_hints: List[str] = field(default_factory=list)
    tone: str = ""
    strength: str = ""
    noise_discarded: List[str] = field(default_factory=list)
    raw: str = ""


@dataclass
class CleanResult:
    is_knowledge: bool
    cleaned: Optional[CleanedCase]
    noise_removed: int = 0
    typed_segments: int = 0
    replies_extracted: int = 0
    note: str = ""


# ---------------------------------------------------------------------------
# ASR 纠错 / 噪音 / 类型切分（保留原始工具）
# ---------------------------------------------------------------------------
def correct_asr(text: str) -> str:
    """高置信同音/口音纠错。只替换明确目标词，不改变风格。"""
    out = text
    for k, v in ASR_FIXES.items():
        out = out.replace(k, v)
    return out


def _clean_noise(text: str) -> Tuple[str, int]:
    count = len(list(NOISE_RE.finditer(text)))
    return NOISE_RE.sub("", text), count


TYPED_RE = re.compile(r"([\u4e00-\u9fff]{1,2})型\s*")


def detect_type_label(text: str) -> Tuple[str, str]:
    m = TYPED_RE.search(text)
    if m and m.start() == 0:
        return m.group(1), text[m.end():].strip()
    return "", text


def extract_typed_replies(text: str) -> Tuple[List[TypedReply], str]:
    """把 `XX型内容...XX型内容` 切成多段；返回 (回复列表, 前置文本)。"""
    text, _ = _clean_noise(text)
    segments: List[TypedReply] = []
    rest: List[str] = []
    buf, current_type, current_buf = "", "", ""
    idx, n = 0, len(text)
    while idx < n:
        m = TYPED_RE.match(text, idx)
        if m:
            if current_type:
                if current_buf.strip():
                    segments.append(TypedReply(current_type, current_buf.strip()))
            elif buf.strip():
                rest.append(buf.strip())
            buf, current_type, current_buf = "", m.group(1), ""
            idx = m.end()
        else:
            ch = text[idx]
            if current_type:
                current_buf += ch
            else:
                buf += ch
            idx += 1
    if current_type:
        if current_buf.strip():
            segments.append(TypedReply(current_type, current_buf.strip()))
    elif buf.strip():
        rest.append(buf.strip())
    return segments, "".join(rest)


def split_by_labels(text: str, labels: List[str]) -> Tuple[List[TypedReply], str]:
    """用已知标签切分 `标签型内容...标签型内容`（结构化导出可靠，不误判正文）。"""
    text, _ = _clean_noise(text)
    if not labels:
        return extract_typed_replies(text)[0], ""
    alts = [re.escape(f"{l}型") for l in labels] + [re.escape(l) for l in labels]
    pattern = re.compile("|".join(alts))
    segments: List[TypedReply] = []
    rest: List[str] = []
    buf, current_label, current_buf = "", "", ""
    idx, n = 0, len(text)
    while idx < n:
        m = pattern.match(text, idx)
        if m:
            if current_label:
                if current_buf.strip():
                    segments.append(TypedReply(current_label, current_buf.strip()))
            elif buf.strip():
                rest.append(buf.strip())
            buf, current_label, current_buf = "", m.group(0).rstrip("型"), ""
            idx = m.end()
        else:
            ch = text[idx]
            if current_label:
                current_buf += ch
            else:
                buf += ch
            idx += 1
    if current_label:
        if current_buf.strip():
            segments.append(TypedReply(current_label, current_buf.strip()))
    elif buf.strip():
        rest.append(buf.strip())
    return segments, "".join(rest)


def _clean_trigger(trigger: str) -> str:
    for fw in QUESTION_FRAME_RE.findall(trigger):
        trigger = trigger.replace(fw, "", 1)
    return trigger.strip()


# ---------------------------------------------------------------------------
# 语义校验：trigger 恢复 / scope 分类 / 失配判定
# ---------------------------------------------------------------------------
def infer_relation(text: str) -> str:
    for rel, kws in RELATION_ALIASES.items():
        if any(k and k in text for k in kws):
            return rel
    return ""


def classify_scope(trigger: str, response: List[str]) -> Tuple[str, List[str], List[str]]:
    """区分"谁说的/在什么场景说的"与"核心回应逻辑是否通用"。

    返回 (scope, scene_constraints, relation_constraints)。
    关键：**领域由 response 的真实语义决定**（response 里出现"加班/绩效/分手/催婚"等强场景词才绑定领域），
    关系词（同事/领导/对象/亲戚…）只决定 relational，不强行把回答锁死到某个领域。
    - response 含强场景标记（上班/绩效/加班/摸鱼/分手/催婚…）→ domain_specific / scene_specific
    - 无领域标记但存在明确关系 → relational
    - 只是"别人指责你→反打回"这类通用逻辑，且 response 无领域/关系限定 → broad
    """
    hay = trigger + "".join(response)
    # 领域只看强场景标记（response 驱动，兼顾 trigger 里的场景词）
    domains = {dom for dom, ms in DOMAIN_MARKERS.items() if any(m and m in hay for m in ms)}
    # 关系：从 trigger/response 里的关系词推断
    rels = []
    for w in RELATION_WORDS:
        if w in hay:
            mapped = _relation_of(w)
            if mapped and mapped not in rels:
                rels.append(mapped)
    rel = rels[0] if rels else ""

    # 平级关系（同事/室友/朋友）的指责通常跨关系通用 → broad；权力/亲密/家庭关系 → relational
    if rel in GENERIC_PEER:
        return "broad", [], []

    rel_constraints: List[str] = [rel] if rel else []
    scene_constraints: List[str] = []

    if domains:
        dom = sorted(domains)[0]
        scene_constraints = [dom]
        return "domain_specific", scene_constraints, rel_constraints

    if rel_constraints:
        return "relational", scene_constraints, rel_constraints

    return "broad", scene_constraints, rel_constraints


def _relation_of(word: str) -> str:
    for rel, kws in RELATION_ALIASES.items():
        if word in kws:
            return rel
    return word


def generalize_trigger(trigger: str, scope: str) -> str:
    """broad 案例：把具体角色归一为泛化角色（同事→别人），让换关系也能召回。

    注意：scope 不是替代 trigger 泛化的万能字段——只改 scope 不改 canonical_trigger，
    检索（按 trigger/query 匹配）仍然召回不到换关系的提问。
    仅 broad 泛化；relational/domain_specific 保留原关系/场景信息。
    """
    if scope != "broad":
        return trigger
    t = trigger
    for actor, generic in ACTOR_GENERIC.items():
        if actor in t:
            t = t.replace(actor, generic, 1)
            break
    return t


def gen_query_examples(trigger: str, scope: str) -> List[str]:
    """生成用户可能真实输入的问法；broad 案例自动覆盖跨关系（室友/同事/朋友/家人）。"""
    if not trigger:
        return []
    base = trigger.strip(" ？?，,。")
    examples: List[str] = []
    if scope == "broad":
        core = generalize_trigger(base, scope)
        for sub in CROSS_RELATIONS:
            q = core.replace("别人", sub, 1) if "别人" in core else core
            for frame in ("{q}怎么回", "{q}怎么怼", "遇到{q}怎么办"):
                examples.append(frame.format(q=q))
    else:
        for frame in ("{b}怎么回", "{b}怎么怼", "{b}怎么办"):
            examples.append(frame.format(b=base))
    return list(dict.fromkeys(examples))[:12]


def _trigger_response_overlap(trigger: str, replies: List[str]) -> float:
    """trigger 与 reply 是否存在语义关联（用公共双字组/关键词近似）。"""
    if not trigger or not replies:
        return 0.0
    tb = _bigrams(trigger)
    best = 0.0
    for r in replies:
        rb = _bigrams(r)
        if tb & rb:
            best = max(best, len(tb & rb) / max(1, len(tb | rb)))
        # 关键词命中：trigger 里的实词出现在 reply
        for t in _tokenize(trigger):
            if len(t) >= 2 and t in r:
                best = max(best, 0.5)
    return best


def _bigrams(text: str) -> set:
    text = text.replace(" ", "")
    return {text[i:i + 2] for i in range(len(text) - 1) if len(text[i:i + 2]) == 2}


def _tokenize(text: str) -> List[str]:
    return [t for t in re.split(r"[\s，。！？!?（）()【】\"'、\-_]+", text) if t]


def derive_canonical_trigger(title: str, lead: str, replies: List[str],
                             fallback: str = "") -> Tuple[str, float]:
    """综合 标题 + 逐字稿前置句 + 回复 + 常识，恢复完整用户场景。

    优先用标题（通常比逐字稿准，含更完整场景），去除提问框架词，做 ASR 纠正；
    若标题缺失/过短，用前置句；再退到 fallback。返回 (trigger, 置信度 0~1)。
    """
    candidates = []
    for cand in (title, lead, fallback):
        cand = (cand or "").strip()
        if cand:
            candidates.append(correct_asr(_clean_trigger(cand)))
    if not candidates:
        return "", 0.0
    trig = candidates[0]
    conf = 0.9 if title and len(title) >= 6 else (0.7 if candidates[0] else 0.4)
    return trig, conf


def build_case_from_text(raw: str, title: str = "", labels: Optional[List[str]] = None) -> CleanResult:
    """把一段脏文本清洗成案例候选（语义校验版）。"""
    # 1) 去噪
    text, noise = _clean_noise(raw)
    # 2) 切分回复（结构化标签优先，否则通用 XX型）
    if labels:
        typed, rest = split_by_labels(raw, labels)
    else:
        typed, rest = extract_typed_replies(raw)
    replies = [correct_asr(t.text).strip() for t in typed if t.text.strip()]
    hints = [t.type_label for t in typed if t.type_label]
    lead = rest.strip()

    # 3) canonical_trigger：标题优先，综合上下文与常识恢复
    trigger, conf = derive_canonical_trigger(title, lead, replies,
                                             fallback=lead or "".join(replies)[:20])
    if not replies and not trigger:
        return CleanResult(is_knowledge=False, cleaned=None, noise_removed=noise,
                           note="未识别为知识素材（无回复也无可辨识触发语）")

    # 4) 失配判定：trigger 与 reply 明显对不上且无法恢复 → needs_review
    #    有权威标题时信任标题（标题通常比逐字稿准）；无标题且回复与触发语近零语义关联 → 标记。
    status = "verified"
    reason = ""
    overlap = _trigger_response_overlap(trigger, replies)
    if not replies:
        status = "needs_review"
        reason = "未能从素材中拆出有效回复"
    elif not title and overlap < 0.03:
        status = "needs_review"
        reason = "trigger↔response 语义无法对应，且无标题/上下文可恢复"

    # 5) scope / 约束
    scope, scene_constraints, rel_constraints = classify_scope(trigger, replies)

    # 5b) 泛化：broad 案例把 canonical_trigger 的角色归一为"别人"，并生成跨关系问法
    #     （scope 不是万能字段——不改 trigger/query 检索就召回不到换关系的提问）
    gen_trigger = generalize_trigger(trigger, scope)
    qex = gen_query_examples(gen_trigger, scope)

    # 6) 关键词（检索辅助；通用词排除）
    kw = []
    for t in _tokenize(trigger + "".join(replies)):
        if len(t) >= 2 and t not in ("怎么", "如何", "应该", "一个", "就是", "这个", "那些"):
            kw.append(t)
    kw = list(dict.fromkeys(kw))[:8]

    strength = "中"
    if any(k in (trigger + "".join(replies)) for k in ["创飞", "裁员", "威胁", "暴击", "绝杀", "反杀", "狠", "滚", "死"]):
        strength = "高"
    tone = "犀利" if strength == "高" else ("撩" if "暧昧" in scope or any(k in trigger for k in ["crush", "撩"]) else "中")

    cleaned = CleanedCase(
        canonical_trigger=gen_trigger,
        intent=_infer_intent(trigger),
        response=replies,
        scope=scope,
        scene_constraints=scene_constraints,
        relation_constraints=rel_constraints,
        keywords=kw,
        status=status,
        needs_review_reason=reason,
        query_examples=qex,
        strategy_hints=hints,
        tone=tone,
        strength=strength,
        noise_discarded=[],
        raw=raw,
    )
    return CleanResult(is_knowledge=True, cleaned=cleaned, noise_removed=noise,
                       typed_segments=len(typed), replies_extracted=len(replies),
                       note="清洗成功" if status == "verified" else f"标记 needs_review：{reason}")


def _infer_intent(trigger: str) -> str:
    """把触发语概括为用户真正想解决的问题。"""
    trigger = trigger.strip(" ？?，,。")
    if any(k in trigger for k in ["催婚", "对象", "感情", "恋爱", "关心"]):
        return "被追问/揶揄，想一句话挡回去"
    if any(k in trigger for k in ["讨厌", "不喜欢", "排斥", "烦"]):
        return "回应别人的讨厌/排斥，不想撕破脸又想扳回一局"
    if any(k in trigger for k in ["敏感", "玻璃心", "想太多"]):
        return "回应别人对你敏感/想太多的指责"
    if any(k in trigger for k in ["关系户", "靠关系", "走后门", "拍马屁"]):
        return "回应别人对你靠关系/走后门的质疑"
    if any(k in trigger for k in ["crush", "暧昧", "已读不回", "不说话", "冷淡"]):
        return "打破冷场/试探对方，拉近距离"
    if any(k in trigger for k in ["分手"]):
        return "应对对方提分手的试探，扳回一局"
    return "被人用话术攻击/揶揄，想有力回应"
