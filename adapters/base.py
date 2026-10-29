"""adapters/base.py — Provider 抽象接口（Core 只依赖这里，不依赖具体实现）。

MVP 全部用 Markdown 实现（见 markdown_provider.py）；未来可替换为
SQLite / Vector DB / Mem0 / 外部"语气学习 Skill"，Core 不需要重写。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 数据模型（跨 Provider 通用）
# ---------------------------------------------------------------------------
@dataclass
class UserStyle:
    """用户当前推荐表达风格（行为记录，非空洞人格画像）。"""
    tone: str = ""                 # 语气：犀利 / 阴阳 / 稳 / 撩 / 心疼 ...
    length: str = "short"          # 长度偏好：short / medium
    strength: str = "medium"       # 默认强度：stable / medium / strong
    no_ai_tone: bool = True        # 是否要求去 AI 味
    name: str = ""                 # 用户称呼（首问一次后记忆；自然使用，不机械每句叫）
    notes: List[str] = field(default_factory=list)   # 实际行为观察


@dataclass
class UserFacingResponse:
    """用户唯一能看到的内容（User-Facing Response Layer 的输出）。

    绝不允许携带任何内部实现字段（case_id / score / scope / fallback / retriever /
    knowledge / source / version / provider / debug 等）。只包含：
      greeting_needs_name  首次接触且未记住称呼时，自然询问一次
      lead                 轻量场景判断 + 情绪回应（动态生成，非模板）
      suggestion           一句话建议（语气/强度方向），可省略
      replies              可直接发送的话术（多个强度版本）
    """
    greeting_needs_name: bool = False
    lead: str = ""
    suggestion: str = ""
    replies: List[str] = field(default_factory=list)


@dataclass
class Case:
    """知识库案例 = 可复用的话术知识（具体怎么说 + 适用边界）。

    关键原则：
    - 不记录来源（博主名/地址/视频链接属数据溯源，放 raw/ 归档层，不进知识库与检索）。
    - 不把通用回应锁死在一个场景；用 scope + constraints 描述适用范围。
    - canonical_trigger 是经过语义纠正的"用户会怎么问"，不是机械的逐字稿片段。
    """
    case_id: str
    canonical_trigger: str            # 标准化后的完整用户场景（ASR 纠正/补全）
    intent: str                       # 用户真正想解决的问题
    response: List[str]               # 最终可用话术（保留原始表达风格，不做规范化）
    scope: str = "broad"              # broad / relational / scene_specific / domain_specific
    scene_constraints: List[str] = field(default_factory=list)    # 有特殊场景才写，无则不设
    relation_constraints: List[str] = field(default_factory=list) # 无明确限制不人为添加
    keywords: List[str] = field(default_factory=list)
    status: str = "verified"          # verified / needs_review / rejected
    query_examples: List[str] = field(default_factory=list)       # 用户可能实际输入的问法
    usage: str = ""                   # 使用边界
    tone: str = ""
    strength: str = ""
    strategy_refs: List[str] = field(default_factory=list)


@dataclass
class CaseHit:
    """候选召回 + 场景适配得分（供上层决定直接引用/降权/改写）。"""
    case: Case
    semantic: float = 0.0      # 语义匹配分（trigger/intent/query_examples/keywords）
    intent: float = 0.0        # 意图匹配分
    scene_fit: float = 0.0     # 场景适配分
    relation_fit: float = 0.0  # 关系适配分
    scope_penalty: float = 0.0 # 跨领域误用惩罚
    total: float = 0.0
    path: str = ""


@dataclass
class Strategy:
    """知识库策略 = 为什么这样说（可迁移的表达规律）。"""
    id: str
    name: str
    description: str
    how_to_use: str
    applicable_scenes: List[str]
    avoid_when: str = ""
    confidence: str = "candidate"          # candidate | stable
    examples: List[str] = field(default_factory=list)


@dataclass
class RetrieveBundle:
    """一次嘴替生成所需的全部检索结果，按优先级组织。"""
    style: Optional[UserStyle] = None
    people: List[str] = field(default_factory=list)
    events: List[str] = field(default_factory=list)
    feedback: List[str] = field(default_factory=list)
    cases: List[Case] = field(default_factory=list)
    case_hits: List[CaseHit] = field(default_factory=list)   # 带场景适配得分的候选（供匹配分级/阈值）
    strategies: List[Strategy] = field(default_factory=list)


@dataclass
class RuntimeState:
    """开发调试用运行状态（与用户输出完全隔离，绝不进入最终回答）。"""
    KB_AVAILABLE: bool = False
    KB_CALLED: bool = False
    KB_SEARCH_MS: float = 0.0
    CANDIDATES: int = 0
    ANSWER_MATCH: str = "none"        # high / medium / low / none
    PATTERN_MATCH: int = 0
    ROUTE: str = ""
    TOTAL_MS: float = 0.0


@dataclass
class QueryAnalysis:
    """问题标准化：场景/关系/意图/强度 的轻量提取（供路由与生成参考）。"""
    relation: str = ""                 # 归一关系（同事/领导/室友/恋人…）
    domains: List[str] = field(default_factory=list)
    is_accusation: bool = False        # 对方"说我X"式指责
    wants_reply: bool = False          # 在求回应（怎么回/怎么怼/怎么办…）
    strength_asked: str = ""           # 用户是否点明强度（稳/冷/狠）


@dataclass
class StyleSpec:
    """从知识库案例自动归纳的整体话术风格（无匹配时也用这个生成，而非 AI 腔）。"""
    tone: str = ""                     # 主要语气（冷/阴阳/犀利…）
    length: str = "short"
    aggression: str = "medium"
    structures: List[str] = field(default_factory=list)   # 高频回应结构
    forbid: List[str] = field(default_factory=list)       # 禁止的 AI 劝架腔
    sample: List[str] = field(default_factory=list)       # 风格样例（话术）
    source_case_count: int = 0


@dataclass
class GenerationPackage:
    """一次"检索 → 选择 → 生成"所需的全部素材。只对上层(Agent/测试)提供；内部字段仅供调试。"""
    query: str = ""
    analysis: Optional[QueryAnalysis] = None
    style: Optional[UserStyle] = None
    people: List[str] = field(default_factory=list)
    events: List[str] = field(default_factory=list)
    feedback: List[str] = field(default_factory=list)
    answer_candidates: List[CaseHit] = field(default_factory=list)   # 已带分级标记
    patterns: List[Strategy] = field(default_factory=list)           # 第二层：回应思路
    style_spec: Optional[StyleSpec] = None                            # 全局风格规范
    language_refs: List[str] = field(default_factory=list)           # 相近案例原文=语感参考（生成时借鉴其"扣住攻击点+一句噎回"的语言质感，不直引）
    lessons: List[str] = field(default_factory=list)                  # 长期回怼经验（knowledge/style/lessons.md）：生成时参照的"为什么这样说更好"规则
    state: Optional[RuntimeState] = None


# ---------------------------------------------------------------------------
# Provider 接口
# ---------------------------------------------------------------------------
class MemoryProvider(ABC):
    @abstractmethod
    def get_user_style(self) -> UserStyle: ...
    @abstractmethod
    def set_preference(self, note: str) -> None: ...
    @abstractmethod
    def get_name(self) -> str: ...
    @abstractmethod
    def set_name(self, name: str) -> None: ...
    @abstractmethod
    def upsert_person(self, name: str, content: Dict[str, Any]) -> None: ...
    @abstractmethod
    def upsert_event(self, key: str, content: Dict[str, Any]) -> None: ...
    @abstractmethod
    def add_feedback(self, note: str) -> None: ...
    @abstractmethod
    def add_candidate(self, note: str, category: str) -> None: ...
    @abstractmethod
    def promote(self, note: str, target: str) -> None: ...


class KnowledgeProvider(ABC):
    @abstractmethod
    def list_cases(self) -> List[Case]: ...
    @abstractmethod
    def get_case(self, case_id: str) -> Optional[Case]: ...
    @abstractmethod
    def upsert_case(self, case: Case) -> None: ...
    @abstractmethod
    def list_strategies(self) -> List[Strategy]: ...
    @abstractmethod
    def get_strategy(self, strategy_id: str) -> Optional[Strategy]: ...
    @abstractmethod
    def upsert_strategy(self, strategy: Strategy) -> None: ...
    @abstractmethod
    def search_cases(self, text: str, top_k: int = 6) -> List[Case]: ...
    @abstractmethod
    def search_case_hits(self, text: str, top_k: int = 6) -> List[CaseHit]: ...
    @abstractmethod
    def search_strategies(self, text: str, top_k: int = 4) -> List[Strategy]: ...


class Retriever(ABC):
    """按优先级检索 用户风格/人物/事件/反馈/案例/策略。"""
    @abstractmethod
    def retrieve_user(self, user_input: str, top_k: int = 6) -> RetrieveBundle: ...


class RetrievalService(ABC):
    """统一知识检索适配器：Agent/核心只认识这一个入口，不猜知识库在哪。

    build_package 一次完成：问题标准化 → 强制检索答案候选 → 召回回应思路(pattern) →
    归纳全局风格 → 返回 GenerationPackage（含运行状态与耗时）。
    底层可接 Markdown / SQLite / 向量库 / Mem0 / 外部语气 Skill，上层接口不变。
    """

    @abstractmethod
    def available(self) -> bool: ...
    @abstractmethod
    def build_package(self, user_input: str, top_k: int = 6) -> GenerationPackage: ...
    # 供宿主/工具直接调用的知识操作（Agent 单次快速取用，避免逐文件读取）
    @abstractmethod
    def search(self, query: str, top_k: int = 6) -> List[CaseHit]: ...
    @abstractmethod
    def get_patterns(self, query: str, top_k: int = 4) -> List[Strategy]: ...


class FeedbackProvider(ABC):
    @abstractmethod
    def record(self, user_input: str, ai_output: str, user_action: str) -> None: ...


class StyleProvider(ABC):
    """返回用户当前推荐表达风格。未来可替换为外部"语气学习 Skill"。"""
    @abstractmethod
    def current_style(self) -> UserStyle: ...


class ContextProvider(ABC):
    """提供当前对话上下文（本轮 + 最近几轮）。默认宿主注入最小实现。"""
    @abstractmethod
    def recent_context(self) -> List[str]: ...


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------
# 通用场景/关系词：不算"区分性"命中，也不作为场景/关系惩罚的依据
GENERIC_KEYS = {
    "职场", "同事", "领导", "情感", "社交", "家庭", "朋友", "一般", "回应",
    "亲密/暧昧", "恋人", "暧昧对象", "陌生人/长辈", "亲戚", "回怼",
    "室友", "舍友", "对象", "伴侣", "家人", "长辈", "老板", "上司",
    # 通用攻击"方式词"（不区分话题，只描述"对方在阴阳/骂/影射我"）——
    # 若把它们当关键词，任何"阴阳我/骂我"的 query 都会误召回该案例 → 必须降权
    "阴阳", "阴阳怪气", "内涵", "影射", "怼", "骂", "嘲讽", "打击", "瞧不起", "奚落", "挤兑",
}

# 领域强场景标记：真正表示"绑定某领域"的概念词。
# 注意：关系词（同事/领导/对象/亲戚…）不是领域标记——"谁说的"不等于"回答属于哪个领域"。
DOMAIN_MARKERS = {
    "职场": ["上班", "公司", "绩效", "加班", "打卡", "工位", "摸鱼", "升职", "职场", "离职", "裁员", "带薪", "项目"],
    "情感": ["恋爱", "分手", "吃醋", "暧昧", "已读不回", "撒娇", "求婚", "心动", "哄对象"],
    "家庭": ["催婚", "结婚", "相亲", "过年"],
    "校园": ["老师", "同学", "宿舍", "上课"],
}

# 关系词：指示"谁说的/与谁有关"，本身不决定领域（可决定 relational 或触发检索过滤）
RELATION_WORDS = [
    "同事", "领导", "老板", "上司", "manager", "室友", "舍友", "朋友", "哥们", "闺蜜",
    "对象", "男朋友", "女朋友", "crush", "亲戚", "大爷", "长辈", "陌生人", "家人",
]

# 常用关系别名 → 归一
RELATION_ALIASES = {
    "同事": ["同事", "同部门", "同事关系"],
    "领导": ["领导", "老板", "上司", "manager"],
    "室友": ["室友", "舍友", "合租"],
    "朋友": ["朋友", "哥们", "闺蜜", "好友"],
    # "没对象/找对象/还没对象"是单身话题，不是恋人关系，不能触发亲密建议
    "恋人": ["对象说", "我对象", "对象嫌", "对象跟", "对象老", "男朋友", "女朋友", "伴侣", "恋人"],
    "暧昧": ["crush", "暧昧对象", "喜欢的人"],
    "亲戚": ["亲戚", "亲戚家", "长辈", "亲戚过年"],
    "陌生人": ["陌生人", "大爷", "路人", "阿姨"],
}


def dict_to_style(d: Dict[str, Any]) -> UserStyle:
    return UserStyle(
        tone=str(d.get("tone", "")),
        length=str(d.get("length", "short")),
        strength=str(d.get("strength", "medium")),
        no_ai_tone=bool(d.get("no_ai_tone", True)),
        name=str(d.get("name", "")),
        notes=list(d.get("notes", [])),
    )
