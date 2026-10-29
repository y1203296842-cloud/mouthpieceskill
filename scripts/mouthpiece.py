"""scripts/mouthpiece.py — 嘴替核心编排（参考实现 / 测试 harness）。

职责：
  1) 判定进入哪个工作模式（MOUTHPIECE / KNOWLEDGE_INGESTION / MEMORY_WRITE / FEEDBACK）。
  2) MOUTHPIECE：走「强制知识检索 → 匹配分级 → 四级路由 → 生成」的完整链路。

四级路由（对应 SKILL.md 与 references/knowledge.md）：
  answer_direct     有高匹配现成答案 → 自动选择最贴合的 → 直引/微调
  answer_adapt      有中匹配答案 → 参考策略按当前场景改写，不硬直引
  move_generate     无高/中匹配 → 按招式库驱动生成（冲突类型→推荐招式→按骨架生成→自检）

设计要点：
  - Core 只依赖 RetrievalService / GenerationPackage 接口（adapters/base.py），不依赖存储实现；
    换 Markdown → SQLite / 向量库 / Mem0 / 假 Provider，Core 不改。
  - 知识库可用时【强制检索】（gatekeeper），不允许模型"觉得没必要就不用"。
  - 检索走索引 + 单次打包（见 MarkdownRetrievalService.build_package），不逐文件扫描、不把全库塞上下文。
  - 用户永远只看到最终话术；case_id/scope/版本/来源/匹配分等一律留在 RuntimeState（调试层），不进回复。
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.base import (
    CaseHit, GENERIC_KEYS, GenerationPackage, RetrievalService, RuntimeState, StyleSpec,
    UserFacingResponse, UserStyle, QueryAnalysis,
)
from adapters.markdown_provider import MarkdownRetrievalService
from scripts.capability import capability_generate, detect_capability
from scripts.move_engine import move_generate
from scripts.preference import load_profile
from scripts.plan import _relation
from scripts.attack import _not_confirm_premise as _no_confirm, _kb_direct_safe, extract_trait

# 内部实现词：任何一条回复命中即剔除（用户永远看不到内部工作流）
_INTERNAL_WORDS = (
    "知识库", "case", "scope", "版本", "来源", "博主", "检索", "召回", "匹配分", "fallback",
    "引擎", "维度", "规则库", "规则", "策略", "招式", "冲突类型", "provider", "embedding",
    "score", "debug", "KB_AVAILABLE", "v1.", "工作流", "内部", "索引", "候选", "模板",
)


def _user_safe_replies(res: "MouthpieceResult") -> List[str]:
    """User-Facing 兜底：任何输出路径都必须——无内部词 + 不隐含确认攻击前提。"""
    trait = extract_trait((res.context_used or {}).get("query", "")) if res.context_used else ""
    out = []
    for ln in res.replies or []:
        if not ln or not isinstance(ln, str):
            continue
        if any(w in ln for w in _INTERNAL_WORDS):
            continue
        if not _no_confirm(ln, trait, False):
            continue
        out.append(ln)
    return out or ["先别急着给我下定义，你倒是把话说清楚。"]

# 模式判定关键词
INGEST_KW = ["加入知识库", "知识库", "嘴替知识", "采集", "原始文案", "逐字稿", "学习这批", "存起来", "这批文案", "洗稿"]
MEMORY_KW = ["记住", "记一下", "记住这个", "记个事"]
FEEDBACK_KW = ["太像ai", "太像ai了", "太长了", "太正式", "不喜欢这种", "换一个", "太客气", "太官方", "太端着了"]
# 恢复默认风格：清除当前用户偏好画像，回落 Skill 内置默认能力
PREF_RESET_KW = ["恢复默认", "还原默认", "回到默认", "不用特殊风格", "取消我的偏好", "清除我的偏好", "不要个性化"]
# 长期偏好修改：如"以后给我狠一点 / 我不喜欢太脏的话 / 以后叫我小明(NAME_WRITE 先行)"
PREF_SET_LEAD = ["以后", "往后", "我习惯", "我一直", "从今往后"]
STYLE_INTENT_KW = ["狠", "冷", "幽默", "阴阳", "客气", "软", "短句", "短一点", "长一点", "别太脏", "不脏",
                   "凶", "犀利", "温柔", "正经", "官方", "有梗", "梗", "直接", "含蓄", "低调", "高调"]
# 用户告知称呼：叫我X / 就叫X / 称呼我X / 名字叫X ...
NAME_GIVE_RE = re.compile(r"(?:叫我|就叫|叫|称呼我|喊我|名字叫)[\"'“”]*([\u4e00-\u9fffa-zA-Z0-9]{1,10})")
# 称呼收尾的语气词要去掉（"叫我小明吧"→小明），只保留实际名字
_NAME_STRIP = "吧啊哦哈呀的呢啦哟"


@dataclass
class MouthpieceResult:
    mode: str = ""
    tier: str = ""                      # high / medium / low / none
    route: str = ""                     # answer_direct / answer_adapt / pattern_generate / style_generate / name_write
    direct_cited: bool = False
    strategy_used: str = ""
    strategy_id: str = ""
    replies: List[str] = field(default_factory=list)
    context_used: dict = field(default_factory=dict)
    best_case_id: str = ""
    analysis: Optional[QueryAnalysis] = None
    style: Optional[UserStyle] = None
    style_spec: Optional[StyleSpec] = None
    state: Optional[RuntimeState] = None    # 仅供调试，绝不进入用户可见回复
    note: str = ""                          # 开发/调试注记，仅在 --debug 输出

    def user_view(self) -> UserFacingResponse:
        """User-Facing Response Layer：只输出用户该看到的内容，隔离所有内部状态。

        lead/suggestion 由场景动态生成（见 _scenario_sense）；replies 是可直接发送的话术。
        绝不携带 case_id / score / scope / fallback / retriever / knowledge / 版本 / 来源 等内部字段。
        """
        out = UserFacingResponse()
        style = self.style or UserStyle()
        name = (style.name or "").strip()
        out.replies = _user_safe_replies(self)
        if self.mode == "NAME_WRITE":
            out.lead = f"好，{name or '这个称呼'}记下了，以后就这么叫你。"
            out.replies = []
            return out
        if self.mode != "MOUTHPIECE":
            # 能力路由类（BLESSING/HIGH_EQ/RELATION 生成）：回复本身就是交付物，不需要 lead
            if self.route.endswith("_generate"):
                out.lead = ""
            else:
                out.lead = self.replies[0] if self.replies else ""
            return out
        lead, suggestion = _scenario_sense(self)
        if not name:
            out.greeting_needs_name = True
            out.lead = f"{lead}（对了，我该怎么称呼你比较好？以后好照着你来。）" if lead else "我该怎么称呼你比较好？以后好照着你的习惯来。"
        else:
            # 称呼是自然人际元素：只在 lead 开头用一次，不塞进每条话术
            out.lead = lead
        out.suggestion = suggestion
        return out


def classify_mode(user_input: str) -> str:
    low = user_input.lower()
    if NAME_GIVE_RE.search(user_input):
        return "NAME_WRITE"
    if any(k in user_input for k in INGEST_KW) or any(k in low for k in ("douyin", "xiaohongshu", "md")):
        return "KNOWLEDGE_INGESTION"
    if any(k in user_input for k in MEMORY_KW):
        return "MEMORY_WRITE"
    if any(k in user_input for k in PREF_RESET_KW):
        return "PREF_RESET"
    if any(k in user_input for k in PREF_SET_LEAD) and any(k in user_input for k in STYLE_INTENT_KW):
        return "PREF_SET"
    if any(k in low for k in FEEDBACK_KW):
        return "FEEDBACK"
    return "MOUTHPIECE"


# ---- 匹配分级（严格直引判定：主题 + 意图方向都对得上，才允许直引） ----
ACCUSATION_MARKERS = ["别人", "同事", "室友", "舍友", "朋友", "领导", "老板", "上司", "manager",
                      "对象", "男朋友", "女朋友", "crush", "亲戚", "家人", "长辈",
                      "骂", "阴阳", "指责", "嫌弃", "内涵", "说我", "说你", "说他", "说她", "使唤", "嘲笑", "催",
                      "你是不是", "你是不是个"]
RESPONSE_INTENT_WORDS = ["怼", "怎么回", "怎么怼", "怎么办", "回击", "反击", "挡回去", "回他", "回她", "接话", "怼回去"]
POWER_ACCUSER = ["领导", "老板", "上司", "manager", "上级"]
RESPOND_INTENT_KW = ["回应", "怼", "挡", "反杀", "化解", "阴阳", "回击", "扳回", "守住", "敷衍", "接", "反打"]
ACTOR_WORDS = {"别人", "同事", "上级", "领导", "对象", "亲戚", "长辈", "陌生人", "家人", "室友", "朋友", "自己", "对方", "他", "她"}

GENERIC_BIGRAMS = {
    "别人", "同事", "室友", "舍友", "朋友", "领导", "老板", "上司", "对象", "男朋友", "女朋友",
    "亲戚", "家人", "长辈", "陌生人", "自己", "对方", "喜欢", "好感", "如何", "应该", "一个",
    "这个", "那些", "哪个", "什么", "那句", "说你", "说我", "说他", "说她", "你说", "我说",
    "怎么", "怎么回", "怎么怼", "怎么办", "怼回", "回去", "一句", "让我", "让她", "把他",
    "跟她", "然后", "其实", "但是", "因为", "所以", "感觉", "觉得", "好像", "有点", "试探",
    "拉近", "距离", "打破", "冷场", "对象", "表达", "意思", "一句", "跟他", "夸你", "夸我",
    "么办", "么回", "么怼", "么接", "么整", "么样", "如何",
    "我怎", "你怎", "怎回", "怎怼", "怎接", "怎说", "怎整",
}


def _bigrams(text: str) -> set:
    text = re.sub(r"[^\u4e00-\u9fff]", "", text)
    return {text[i:i + 2] for i in range(len(text) - 1) if len(text[i:i + 2]) == 2}


def _distinctive_bigrams(case) -> set:
    src = (case.canonical_trigger or "") + " " + " ".join(case.query_examples or [])
    bs = _bigrams(src)
    return {b for b in bs if b not in GENERIC_BIGRAMS and b not in ACTOR_WORDS and len(b) == 2}


def _is_cjk(w: str) -> bool:
    return bool(re.fullmatch(r"[\u4e00-\u9fff]+", w))


def _subject_overlap(case, user_input: str) -> bool:
    """主题是否真正对得上：用 case.keywords 里的辨识性实词（只留汉字词），
    避免"同事说X"关系框架片段或英文 crush 造成误配（如"同事说我太抠"≠"太敏感"）。"""
    kws = [k for k in (case.keywords or []) if _is_cjk(k) and k not in GENERIC_KEYS and k not in ACTOR_WORDS]
    if kws:
        return any(k in user_input for k in kws)
    return bool(set(_bigrams(user_input)) & _distinctive_bigrams(case))


def _is_respond_case(case) -> bool:
    return any(k in (case.intent or "") for k in RESPOND_INTENT_KW)


# 言说动词：出现"说/骂/催/嫌…"且引语指向"你"，即"对方对用户说了一句冒犯/要求性的话"，
# 这类输入（"高铁上孩子吵，对方说嫌吵你去坐一等座呀"）隐含回应请求，即使没有"怎么回/怎么怼"。
SPEECH_VERBS = ["说", "讲", "骂", "怼", "催", "问", "嫌", "吐槽", "内涵", "阴阳", "指责", "嘲笑", "开涮", "点我", "阴阳怪气"]


def _direction_ok(case, text: str) -> bool:
    if any(m in text for m in ACCUSATION_MARKERS):
        return True
    if any(w in text for w in RESPONSE_INTENT_WORDS):
        return True
    # 隐含回应请求：言说动词 + 第二人称指向（对方在对用户说话）
    if any(v in text for v in SPEECH_VERBS) and any(t in text for t in ("你", "您")):
        return True
    return False


def _matches_directly(case, user_input: str) -> bool:
    if case.canonical_trigger and (case.canonical_trigger in user_input or user_input in case.canonical_trigger):
        return True
    for q in case.query_examples:
        if q and (q in user_input or user_input in q):
            return True
    if not _subject_overlap(case, user_input):
        return False
    if _is_respond_case(case) and not _direction_ok(case, user_input):
        return False
    return True


def _tier_of(hit: CaseHit, user_input: str) -> str:
    if not _matches_directly(hit.case, user_input):
        return "low"
    c = hit.case
    if any(p in user_input for p in POWER_ACCUSER) and c.scope == "broad":
        return "medium"
    if hit.semantic >= 6.0 and hit.total >= 8.0:
        return "high"
    if hit.semantic >= 3.0 or hit.total >= 5.0:
        return "medium"
    return "low"


def _pick_best(hits: List[CaseHit]) -> Optional[CaseHit]:
    """多候选自动选择（内部决策，绝不让用户选版本/文件夹/答案）：偏好关系贴合 + 总分高 + 更通用。"""
    def key(h):
        return (h.relation_fit, h.total, 1.0 if h.case.scope == "broad" else 0.0)
    return max(hits, key=key) if hits else None


# 攻击特征提取统一走 scripts/attack.extract_trait（本文件不再维护第二份）

# ---- 生成（确定性 harness；生产由 Agent 依据 GenerationPackage 生成，风格见 style_spec） ----
# 点对点原则：只要 trait（被攻击的具体点）可知，改写行必须扣住它——
# 换成任何攻击都成立的"万金油句"（如"你眼光这么准"）不合格，重写。
def _retort_lines(strategy_id: str, trait: str, tone: str) -> List[str]:
    t = trait if trait else ""
    head = f"『{t}』" if t else ""
    if strategy_id == "reverse-premise":
        # 点对点：不认下（"我自己都不知道"），把"打哪儿听来"弹回对方
        if t:
            return [f"你这话是打哪儿听来的？我自己都不知道我{t}。",
                    f"你这么急着给我定性，是怕别人不知道你什么水平吗？"]
        return ["你这话是打哪儿听来的？我自己怎么不知道。",
                "你这么急着给我定性，是怕别人不知道你什么水平吗？"]
    if strategy_id == "shift-onto-them":
        return [f"你这么盯着我{head}这点事，是手上太闲了？",
                f"我{t}不{t}的，你倒先操心起我来了？"]
    if strategy_id == "play-dumb":
        return [f"啊？你说啥，我没听明白。", f"你刚才说的是人话吗？我听岔了。"]
    if strategy_id == "ironic":
        # 点对点：讽刺对方"对我这事太上心"，而不是通用夸他眼光准
        if t:
            return [f"你对我{t}这事这么上心，不去算命可惜了。",
                    f"你倒是挺会挑我{t}说事，这观察力不当侦探可惜。"]
        # 特征未知时：攻击"对方爱挑刺"这个行为本身，绝不使用换成任何攻击都成立的万金油夸句
        return ["你对我身上的事这么上心，自己那摊子收拾明白了吗？",
                "爱挑别人毛病的人，一般都过得不怎么样。"]
    if strategy_id == "aloof":
        return [f"说完了？那我先忙了。", f"嗯，知道了。"]
    if strategy_id == "absurd":
        return [f"那我罪过可大了，回头我把它供起来。", f"你想象力这么丰富，不去写小说可惜了。"]
    if strategy_id == "flirt":
        return [f"你这么在意我，我都要当真了。"]
    if strategy_id == "comfort":
        return [f"辛苦你了，今晚早点休息，有事跟我说。"]
    # 兜底（正常不会走到；给真实话术，绝不暴露"参考知识库策略"等内部信息）
    return ["先别急着给我下定义，你倒是把话说清楚。", "你这么想，是你的事；我不打算按你设定的来。"]


def _style_generate(query: str, analysis, spec: Optional[StyleSpec]) -> List[str]:
    """无答案也无思路：用全局风格规范生成（短/直接/去AI味/无劝架腔）。"""
    conflict = any(w in query for w in ("排挤", "孤立", "针对", "欺负", "瞧不起", "阴阳", "怼", "骂", "嫌弃", "内涵", "阴阳怪气"))
    if (analysis and analysis.is_accusation) or conflict:
        return ["先别急着给我下定义，你倒是把话说清楚。", "你这么想，是你的事；我不打算按你设定的来。"]
    if any(w in query for w in ("安慰", "难过", "失恋", "伤心", "累了")):
        return ["别硬扛，我陪着。", "这事儿不是你的错。"][:1]
    if analysis and analysis.wants_reply:
        return ["直接说重点，我在听。"]
    return ["你这话我记下了，回头再说。"]


# ---- 场景判断（轻量情绪价值 + 建议方向；动态生成，非固定模板；只影响 lead/suggestion，不改核心话术） ----
POWER_REL = {"领导", "老板", "上司", "上级"}
INTIMATE_REL = {"恋人", "暧昧"}
ELDER_REL = {"亲戚"}
MULTI_PERSON_KW = ("多人", "当众", "人多", "公开", "饭局", "聚会", "都在", "开会", "大家", "场合", "一堆人")
DISLIKE_KW = ("讨厌", "烦死", "烦他", "烦死他", "受不了", "恶心", "嫌弃他", "看不惯", "阴阳怪气我")


def _scenario_sense(res: MouthpieceResult) -> tuple:
    """根据 关系/场合/对方行为/记忆，动态给一句场景判断 + 一句话建议。"""
    q = (res.context_used.get("query", "") if res.context_used else "")
    analysis = res.analysis
    rel = analysis.relation if analysis else ""
    lowq = q
    is_acc = bool(analysis and analysis.is_accusation)
    conflict = any(w in lowq for w in ("排挤", "孤立", "针对", "欺负", "瞧不起", "阴阳", "怼", "骂", "嫌弃", "内涵"))
    multi = any(w in lowq for w in MULTI_PERSON_KW)
    power = rel in POWER_REL
    intimate = rel in INTIMATE_REL
    elder = rel in ELDER_REL
    dislike = any(w in lowq for w in DISLIKE_KW)
    # 记忆里的长期厌恶（feedback/人物）只作为"一个因素"，不构成"永远往狠了去"
    memory_dislike = any(w in lowq for w in DISLIKE_KW)

    lead = ""
    if is_acc or conflict:
        lead = "这话对方是冲着你来的，别顺着解释，把他的逻辑直接还回去就行。"
    elif analysis and analysis.wants_reply:
        lead = "这种话不用想太多，简短有力回过去就够了。"
    else:
        lead = "这句话可以直接发，别绕。"
    if multi:
        lead = "人多的时候别让他把你架起来——这句要让旁边人听着也是你有理。"
    elif power:
        lead = "对上位者别硬刚，话软一点但立场不退，免得被抓住把柄。"
    elif elder:
        lead = "长辈那边点到为止，玩笑式带过最体面，别把场面弄僵。"
    elif intimate:
        lead = "跟ta别用狠话，表达不满比怼更重要，力度收着点。"
    if memory_dislike:
        lead = "这人你本来就不待见，不用太给他留面子——但也别失控，稳住反而更有杀伤力。"

    suggestion = ""
    if multi or power:
        suggestion = "稳一点的开场最稳，冷一点/狠一点看你当场能不能兜住。"
    elif intimate:
        suggestion = "先表达感受，再决定要不要带点俏皮，别一上来就上强度。"
    elif elder:
        suggestion = "装傻/玩笑式带过最合适，别把亲戚关系弄僵。"
    elif conflict or is_acc:
        suggestion = "直接或不接前提都行，看你想让他当场下不来台，还是淡淡带过。"
    else:
        suggestion = ""
    return lead, suggestion


class Mouthpiece:
    """嘴替核心。构造时注入 RetrievalService；不注入则用默认 Markdown 实现。
    user_id 用于隔离用户记忆（默认 "default"；环境变量 MOUTHPIECE_USER_ID 可覆盖）。"""

    def __init__(self, service: Optional[RetrievalService] = None, user_id: str = "default") -> None:
        uid = os.environ.get("MOUTHPIECE_USER_ID", user_id or "default").strip() or "default"
        if service is None:
            from adapters.markdown_provider import MarkdownMemoryProvider
            service = MarkdownRetrievalService(memory=MarkdownMemoryProvider(user_id=uid))
        self.service = service
        self.user_id = uid

    def _handle_name_write(self, user_input: str) -> MouthpieceResult:
        m = NAME_GIVE_RE.search(user_input)
        name = (m.group(1).strip() if m else "").rstrip(_NAME_STRIP)
        r = MouthpieceResult(mode="NAME_WRITE", route="name_write")
        # 持久化到用户记忆（通过 RetrievalService 的 set_name；未实现则仅本会话）
        if name:
            setter = getattr(self.service, "set_name", None)
            if callable(setter):
                setter(name)
            r.style = UserStyle(name=name)
            r.replies = [f"好，{name}，我记住了。"]
        else:
            r.replies = ["你想让我怎么称呼你？"]
        r.note = f"name_write: {name or '未识别'}（{self.service.__class__.__name__}）"
        return r

    def respond(self, user_input: str, top_k: int = 6) -> MouthpieceResult:
        mode = classify_mode(user_input)
        if mode == "NAME_WRITE":
            return self._handle_name_write(user_input)
        if mode == "PREF_RESET":
            resetter = getattr(self.service, "reset_prefs", None)
            ok = bool(resetter and callable(resetter) and resetter())
            r = MouthpieceResult(mode="PREF_RESET", route="pref_reset")
            r.replies = ["好，回到默认风格。之前学到的优秀回怼能力还在，只是不再按个人偏好调整了。"]
            r.note = f"pref_reset removed={ok}（{self.user_id}）"
            return r
        if mode == "PREF_SET":
            # 长期偏好修改：记录偏好信号（更新画像）+ 写入该用户风格记忆
            from scripts.preference import apply_feedback
            apply_feedback(user_input)
            prom = getattr(self.service, "remember_style", None)
            if callable(prom):
                prom(user_input)
            r = MouthpieceResult(mode="PREF_SET", route="pref_set")
            r.replies = ["记住了，以后按这个风格来。"]
            r.note = f"pref_set（{self.user_id}）"
            return r
        if mode != "MOUTHPIECE":
            return MouthpieceResult(mode=mode)

        # ---- 能力路由：不同能力用各自资料，绝不跨能力串台 ----
        # 祝福 / 高情商 → 走各自能力入口（骨架级，绝不动回怼资料）；回怼/撩 → 主路径。
        cap = detect_capability(user_input)
        if cap in ("BLESSING", "HIGH_EQ"):
            return MouthpieceResult(mode=cap, route=f"{cap.lower()}_generate",
                                    replies=capability_generate(cap, user_input),
                                    context_used={"query": user_input})

        r = MouthpieceResult(mode="MOUTHPIECE")
        # ---- gatekeeper：知识库可用 → 【强制】走统一检索（一次打包，不逐文件扫描） ----
        pkg = self.service.build_package(user_input, top_k=top_k)
        state = pkg.state or RuntimeState()
        r.state = state
        r.analysis = pkg.analysis
        r.style = pkg.style
        r.style_spec = pkg.style_spec
        r.context_used = {"people": pkg.people, "events": pkg.events, "query": user_input,
                          "language_refs": pkg.language_refs}
        if not state.KB_AVAILABLE:
            # 环境确实没有知识库 → 明确 fallback（state 记录，避免"假装用了知识库"）
            state.ROUTE = "style_generate"
            state.ANSWER_MATCH = "none"
            r.route = "style_generate"
            r.replies = _style_generate(user_input, pkg.analysis, pkg.style_spec)
            r.strategy_used = "知识库风格生成（环境无知识库）"
            r.note = f"KB_AVAILABLE=false → style_generate（{state.TOTAL_MS}ms）"
            return r

        # ---- 匹配分级 → 四级路由 ----
        high = [h for h in pkg.answer_candidates if _tier_of(h, user_input) == "high"]
        med = [h for h in pkg.answer_candidates if _tier_of(h, user_input) == "medium"]
        best = None
        if high:
            best = _pick_best(high)
            route, tier, cited = "answer_direct", "high", True
        elif med:
            best = _pick_best(med)
            route, tier, cited = "answer_adapt", "medium", False
        elif pkg.patterns:
            route, tier, cited = "pattern_generate", "low", False
        else:
            route, tier, cited = "style_generate", "none", False
        state.ANSWER_MATCH = tier
        state.ROUTE = route
        state.PATTERN_MATCH = len(pkg.patterns)
        r.route, r.tier, r.direct_cited = route, tier, cited
        if best:
            r.best_case_id = best.case.case_id

        # ---- 生成 ----
        trait = extract_trait(user_input)
        tone = pkg.style.tone if pkg.style else "冷"
        if route == "answer_direct" and best:
            c = best.case
            r.strategy_id = c.strategy_refs[0] if c.strategy_refs else ""
            r.strategy_used = "高匹配·直引现成回答"
            # 直引前提隔离 ①：纯自嘲/荒诞型 case（无任何反击策略）在被贬低场景不直引
            #（如"喜欢高的"=自嘲接梗案例；用户被贬低"矮"时直引"速冻矮子"=认下前提）
            _RETORT_STRS = ("reverse-premise", "ironic", "shift-onto-them", "aloof",
                            "play-dumb", "kouhui", "fanwen", "pengsha", "libianjie",
                            "bujie", "m1", "m2", "m3", "m4", "m6", "m7", "m8")
            playful = any(x in user_input for x in ("玩梗", "自嘲", "幽默", "开玩笑", "接梗", "接住", "化解"))
            pure_selfdep = bool(c.strategy_refs) and not any(s in _RETORT_STRS for s in c.strategy_refs)
            if trait and pure_selfdep and not playful:
                r.route, r.tier, r.direct_cited = "move_generate", "none", False
                r.strategy_used = "高匹配为自嘲接梗型案例·被贬低场景不直引，降级招式驱动"
                r.replies = move_generate(user_input, pkg.analysis)
                r.note = f"自嘲型 case 被拦截 → move_generate（{state.TOTAL_MS}ms）"
            else:
                replies = list(c.response)
                # 直引前提隔离 ②：逐句过滤复述认下前提的句子
                #（"速冻矮子/拿脚臭说事"= 站在攻击前提上接话；"不敏感还不知道/你鼻子臭"= 放行）
                if trait:
                    kept = [ln for ln in replies if _kb_direct_safe(ln, trait)]
                    if kept:
                        replies = kept
                if not replies:
                    # 全部被过滤 → 不直引，降级为招式驱动（attack 策略树/不接前提）
                    r.route, r.tier, r.direct_cited = "move_generate", "none", False
                    r.strategy_used = "高匹配案例含认下句·降级招式驱动生成"
                    r.replies = move_generate(user_input, pkg.analysis)
                    r.note = f"直引被前提隔离拦截 → move_generate（{state.TOTAL_MS}ms）"
                else:
                    r.replies = replies
                    if pkg.style and pkg.style.length == "short":
                        r.replies = r.replies[:2]
                    elif len(r.replies) > 3:
                        r.replies = r.replies[:3]
                    r.note = f"route=answer_direct best={c.case_id}（{state.TOTAL_MS}ms）"
        elif route == "answer_adapt" and best:
            sid = best.case.strategy_refs[0] if best.case.strategy_refs else ""
            pat = next((s for s in pkg.patterns if s.id == sid), None)
            r.strategy_id = sid
            r.strategy_used = f"中匹配·参考『{pat.name if pat else sid}』按场景改写"
            r.replies = _retort_lines(sid, trait, tone) if sid else ["先别急着给我下定义，你倒是把话说清楚。"]
            r.note = f"route=answer_adapt case={best.case.case_id}（{state.TOTAL_MS}ms）"
        elif route == "pattern_generate":
            pat = pkg.patterns[0]
            r.strategy_id = pat.id
            r.strategy_used = f"无现成答案·按招式库思路『{pat.name if pat else '不接前提'}』生成"
            r.replies = move_generate(user_input, pkg.analysis)
            r.route = "move_generate"
            r.note = f"fallback·move_generate（{state.TOTAL_MS}ms）招式驱动，未硬套知识库"
        else:
            r.strategy_used = "无匹配·按招式库驱动生成（不接前提/反问/扣回等）"
            r.route = "move_generate"
            r.replies = move_generate(user_input, pkg.analysis)
            r.note = f"fallback·move_generate（{state.TOTAL_MS}ms）未硬套知识库"
        # 偏好画像只做风格层微调（强度排序/条数），在自检与 Hard Rules 之后
        r.replies = _apply_profile_bias(r.replies, load_profile())
        # 亲密关系（恋人）场景：过滤"把责任踢回对方"的句子（用户偏好：认错+给确定性，不踢皮球）
        if _relation(user_input) == "恋人":
            r.replies = [ln for ln in r.replies
                         if not any(w in ln for w in ("你心里应该清楚", "你自己心里清楚", "你心里有数", "你应该知道"))]
        return r


def _apply_profile_bias(replies: List[str], profile: Dict) -> List[str]:
    """偏好画像影响输出：强度排序（aggression）与条数（concise）。
    只调整风格层，绝不触碰 Hard Rules（前提隔离/点对点/自检已先行完成）。"""
    if not profile:
        return replies
    dims = profile.get("dims", {})
    agg = dims.get("aggression", {}).get("value", 2.5)
    concise = dims.get("concise", {}).get("value", 2.5)
    _HARD = ("凭什么", "脸呢", "你也", "活明白", "挺闲", "没什么别的好聊", "碍着", "轮不到", "噎")
    _SOFT = ("行，", "可以", "知道了", "我改", "我以后", "抱歉", "辛苦了", "早点")

    def score(ln: str) -> int:
        return sum(1 for w in _HARD if w in ln) - sum(1 for w in _SOFT if w in ln)

    if agg >= 3.2:
        replies = sorted(replies, key=score, reverse=True)
    elif agg <= 2.2:
        replies = sorted(replies, key=score)
    if concise >= 3.5 and len(replies) > 2:
        replies = replies[:2]
    return replies


def run(user_input: str, top_k: int = 6, user_id: str = "default") -> MouthpieceResult:
    return Mouthpiece(user_id=user_id).respond(user_input, top_k=top_k)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="私人嘴替核心（参考 harness）")
    ap.add_argument("input", help="用户输入，如：帮我回一下：你这项目怎么还没做完？")
    ap.add_argument("--debug", action="store_true", help="显示内部调试状态（默认对用户隐藏）")
    ap.add_argument("--user", default="default", help="用户 ID（隔离个人偏好/记忆），默认 default")
    args = ap.parse_args()
    r = run(args.input, user_id=args.user)
    uv = r.user_view()
    if uv.greeting_needs_name:
        print(uv.lead)
        sys.exit(0)
    if uv.lead:
        print(uv.lead)
    if uv.suggestion:
        print(uv.suggestion)
    print("---")
    for line in uv.replies:
        print("  -", line)
    if args.debug:
        s = r.state
        print(f"\n[debug] mode={r.mode} route={r.route} tier={r.tier} KB_AVAILABLE={s.KB_AVAILABLE} "
              f"KB_CALLED={s.KB_CALLED} KB_SEARCH_MS={s.KB_SEARCH_MS} candidates={s.CANDIDATES} "
              f"answer={s.ANSWER_MATCH} patterns={s.PATTERN_MATCH} total={s.TOTAL_MS}ms best={r.best_case_id}")
        print(f"[debug] strategy={r.strategy_used}\n[debug] note={r.note}")
