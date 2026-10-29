"""adapters/fake_provider.py — 内存假 Provider（用于"跨 Provider"测试）。

证明：嘴替 Core 只依赖 adapters/base.py 接口，不依赖 Markdown 实现。
换成这个假 Provider，Core（scripts/mouthpiece.py）无需任何改动即可运行。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import (
    Case, CaseHit, ContextProvider, FeedbackProvider, GenerationPackage,
    KnowledgeProvider, MemoryProvider, QueryAnalysis, Retriever, RetrieveBundle,
    RetrievalService, RuntimeState, Strategy, StyleSpec, UserStyle,
)


class FakeMemoryProvider(MemoryProvider):
    def __init__(self) -> None:
        self.style = UserStyle(tone="犀利", length="short", strength="medium", no_ai_tone=True,
                               notes=["用户喜欢短直接"])
        self._name = ""
        self.people: Dict[str, Dict[str, Any]] = {}
        self.events: Dict[str, Dict[str, Any]] = {}
        self.feedback: List[str] = []
        self.candidates: List[str] = []

    def get_user_style(self) -> UserStyle:
        return UserStyle(tone=self.style.tone, length=self.style.length,
                         strength=self.style.strength, no_ai_tone=self.style.no_ai_tone,
                         name=self._name, notes=list(self.style.notes))

    def get_name(self) -> str:
        return self._name

    def set_name(self, name: str) -> None:
        self._name = (name or "").strip()

    def set_preference(self, note: str) -> None:
        self.style.notes.append(note)

    def upsert_person(self, name: str, content: Dict[str, Any]) -> None:
        self.people[name] = content

    def upsert_event(self, key: str, content: Dict[str, Any]) -> None:
        self.events[key] = content

    def add_feedback(self, note: str) -> None:
        self.feedback.append(note)

    def add_candidate(self, note: str, category: str) -> None:
        self.candidates.append(f"[{category}] {note}")

    def promote(self, note: str, target: str) -> None:
        if target == "style":
            self.style.notes.append(note)


class FakeKnowledgeProvider(KnowledgeProvider):
    def __init__(self) -> None:
        self.cases: List[Case] = [
            Case(case_id="guanxi-fake", canonical_trigger="你是关系户",
                 intent="回应别人对你靠关系的质疑", response=["我还能靠关系让你出去"],
                 scope="broad", keywords=["关系户", "靠关系"], status="verified",
                 tone="犀利", strength="中"),
        ]
        self.strategies: List[Strategy] = [
            Strategy(id="reverse-premise", name="不接对方前提",
                     description="不解释，反打回", how_to_use="反打",
                     applicable_scenes=["职场"], confidence="stable"),
        ]

    def list_cases(self) -> List[Case]:
        return self.cases

    def get_case(self, case_id: str) -> Optional[Case]:
        return next((c for c in self.cases if c.case_id == case_id), None)

    def upsert_case(self, case: Case) -> None:
        self.cases = [c for c in self.cases if c.case_id != case.case_id] + [case]

    def list_strategies(self) -> List[Strategy]:
        return self.strategies

    def get_strategy(self, strategy_id: str) -> Optional[Strategy]:
        return next((s for s in self.strategies if s.id == strategy_id), None)

    def upsert_strategy(self, strategy: Strategy) -> None:
        self.strategies = [s for s in self.strategies if s.id != strategy.id] + [strategy]

    def search_cases(self, text: str, top_k: int = 6) -> List[Case]:
        hits = [c for c in self.cases if any(k in text for k in c.keywords)]
        return hits[:top_k]

    def search_case_hits(self, text: str, top_k: int = 6) -> List[CaseHit]:
        cases = self.search_cases(text, top_k)
        # 真实匹配 → 给出"高匹配"得分，供 Core 按统一阈值判定（跨 Provider 契约：语义/总量为可比刻度）
        return [CaseHit(case=c, semantic=10.0, intent=2.0, scene_fit=1.0,
                        relation_fit=1.0, scope_penalty=0.0, total=12.0) for c in cases]

    def search_strategies(self, text: str, top_k: int = 4) -> List[Strategy]:
        hits = [s for s in self.strategies if any(k in text for k in s.applicable_scenes)]
        return hits[:top_k]


class FakeRetriever(Retriever):
    def __init__(self, memory: Optional[MemoryProvider] = None,
                 knowledge: Optional[KnowledgeProvider] = None) -> None:
        self.memory = memory or FakeMemoryProvider()
        self.knowledge = knowledge or FakeKnowledgeProvider()

    def retrieve_user(self, user_input: str, top_k: int = 6) -> RetrieveBundle:
        return RetrieveBundle(
            style=self.memory.get_user_style(),
            people=[n for n in self.memory.people if n in user_input],
            events=list(self.memory.events.keys()),
            feedback=list(self.memory.feedback),
            cases=self.knowledge.search_cases(user_input, top_k),
            case_hits=self.knowledge.search_case_hits(user_input, top_k),
            strategies=self.knowledge.search_strategies(user_input, 4),
        )


class FakeRetrievalService(RetrievalService):
    """内存假检索服务：证明 Core 只认 RetrievalService 接口，换实现不重写。"""

    def __init__(self, memory: Optional[MemoryProvider] = None,
                 knowledge: Optional[KnowledgeProvider] = None) -> None:
        self.memory = memory or FakeMemoryProvider()
        self.knowledge = knowledge or FakeKnowledgeProvider()

    def available(self) -> bool:
        return bool(self.knowledge.list_cases())

    def set_name(self, name: str) -> None:
        self.memory.set_name(name)

    def get_name(self) -> str:
        return self.memory.get_name()

    def search(self, query: str, top_k: int = 6) -> List[CaseHit]:
        return self.knowledge.search_case_hits(query, top_k=top_k)

    def get_patterns(self, query: str, top_k: int = 4) -> List[Strategy]:
        return self.knowledge.search_strategies(query, top_k=top_k)

    def build_package(self, user_input: str, top_k: int = 6) -> GenerationPackage:
        state = RuntimeState(KB_AVAILABLE=self.available())
        state.KB_CALLED = state.KB_AVAILABLE
        state.CANDIDATES = len(self.knowledge.search_case_hits(user_input, top_k))
        state.PATTERN_MATCH = len(self.knowledge.search_strategies(user_input, 4))
        state.ROUTE = ""
        rel = ""
        if any(w in user_input for w in ("同事", "室友", "朋友", "同学")):
            rel = "同事"
        elif any(w in user_input for w in ("领导", "老板", "上司", "上级")):
            rel = "领导"
        elif any(w in user_input for w in ("对象", "男朋友", "女朋友", "crush", "喜欢的人")):
            rel = "暧昧"
        elif any(w in user_input for w in ("亲戚", "长辈", "过年")):
            rel = "亲戚"
        pkg = GenerationPackage(
            query=user_input,
            analysis=QueryAnalysis(relation=rel,
                                   is_accusation=("说" in user_input), wants_reply=("怎么" in user_input)),
            style=self.memory.get_user_style(),
            people=[n for n in self.memory.people if n in user_input],
            events=list(self.memory.events.keys()),
            feedback=list(self.memory.feedback),
            answer_candidates=self.knowledge.search_case_hits(user_input, top_k),
            patterns=self.knowledge.search_strategies(user_input, 4),
            style_spec=StyleSpec(tone="犀利", length="short", source_case_count=1),
            state=state,
        )
        return pkg


class FakeFeedbackProvider(FeedbackProvider):
    def __init__(self, memory: Optional[MemoryProvider] = None) -> None:
        self.memory = memory or FakeMemoryProvider()

    def record(self, user_input: str, ai_output: str, user_action: str) -> None:
        self.memory.add_feedback(f"{user_action} | {user_input}")


class FakeStyleProvider:
    def __init__(self, memory: Optional[MemoryProvider] = None) -> None:
        self.memory = memory or FakeMemoryProvider()

    def current_style(self) -> UserStyle:
        return self.memory.get_user_style()


class FakeContextProvider(ContextProvider):
    def __init__(self) -> None:
        self._ctx: List[str] = []

    def recent_context(self) -> List[str]:
        return self._ctx

    def push(self, line: str) -> None:
        self._ctx.append(line)


def build_fake_services():
    """返回一套纯内存 Provider 组合（跨 Provider 测试用）。"""
    mem = FakeMemoryProvider()
    kn = FakeKnowledgeProvider()
    return {
        "memory": mem,
        "knowledge": kn,
        "retriever": FakeRetriever(mem, kn),
        "service": FakeRetrievalService(mem, kn),
        "feedback": FakeFeedbackProvider(mem),
        "style": FakeStyleProvider(mem),
        "context": FakeContextProvider(),
    }
