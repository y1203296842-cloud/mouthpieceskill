"""scripts/retrieve.py — 统一知识检索入口（Agent 一次快速调用，不逐文件扫描）。

用法：
  python3 scripts/retrieve.py "同事说我是关系户怎么回"              # 人性化摘要（面向测试/人）
  python3 scripts/retrieve.py "同事说我是关系户怎么回" --json       # 机器可读 GenerationPackage（供 Agent/宿主）
  python3 scripts/retrieve.py "同事说我是关系户怎么回" --debug      # 附带内部运行状态（开发调试用）

说明：
  - 这是上层(Agent/核心)唯一认识的检索入口；底层可接 Markdown/SQLite/向量库/Mem0，
    接口不变（见 adapters/base.RetrievalService）。
  - --json 输出只给 Agent 内部消费；任何来源/版本/case_id/路径字段都绝不进最终用户回答。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.markdown_provider import MarkdownRetrievalService


def _pkg_to_json(pkg) -> dict:
    """GenerationPackage → 紧凑 JSON（保留 pattern 思路与 style_spec，供 Agent 据此生成）。"""
    state = pkg.state
    return {
        "query": pkg.query,
        "analysis": {
            "relation": pkg.analysis.relation if pkg.analysis else "",
            "domains": pkg.analysis.domains if pkg.analysis else [],
            "is_accusation": bool(pkg.analysis and pkg.analysis.is_accusation),
            "wants_reply": bool(pkg.analysis and pkg.analysis.wants_reply),
        },
        "answers": [{
            "id": h.case.case_id,
            "trigger": h.case.canonical_trigger,
            "reply": list(h.case.response[:5]),   # 只给少量高质量候选，控制上下文体积
            "scope": h.case.scope,
            "tone": h.case.tone,
            "strength": h.case.strength,
            "score": round(h.total, 1),
        } for h in pkg.answer_candidates],
        "patterns": [{
            "id": s.id,
            "name": s.name,
            "how_to_use": s.how_to_use,
            "description": s.description,
            "avoid_when": s.avoid_when,
        } for s in pkg.patterns],
        "style_spec": {
            "tone": pkg.style_spec.tone if pkg.style_spec else "",
            "length": pkg.style_spec.length if pkg.style_spec else "short",
            "forbid": pkg.style_spec.forbid if pkg.style_spec else [],
            "sample": pkg.style_spec.sample if pkg.style_spec else [],
        },
        "state": {
            "KB_AVAILABLE": state.KB_AVAILABLE,
            "KB_CALLED": state.KB_CALLED,
            "KB_SEARCH_MS": state.KB_SEARCH_MS,
            "CANDIDATES": state.CANDIDATES,
            "PATTERN_MATCH": state.PATTERN_MATCH,
            "TOTAL_MS": state.TOTAL_MS,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="嘴替统一知识检索入口")
    ap.add_argument("query", help="用户输入/场景")
    ap.add_argument("--top-k", type=int, default=6)
    ap.add_argument("--json", action="store_true", help="输出 GenerationPackage JSON（供 Agent/宿主）")
    ap.add_argument("--debug", action="store_true", help="附带内部运行状态（仅开发调试）")
    args = ap.parse_args()

    svc = MarkdownRetrievalService()
    pkg = svc.build_package(args.query, top_k=args.top_k)

    if args.json:
        print(json.dumps(_pkg_to_json(pkg), ensure_ascii=False, indent=2))
        return

    print(f"== 问题分析 ==")
    a = pkg.analysis
    print(f"  关系:{a.relation} 领域:{a.domains} 指责:{a.is_accusation} 求回应:{a.wants_reply}")
    print(f"== 用户风格 ==")
    if pkg.style:
        print(f"  语气:{pkg.style.tone} 长度:{pkg.style.length}")
    print(f"== 人物 ==", pkg.people if pkg.people else "-")
    print(f"== 事件 ==", pkg.events if pkg.events else "-")
    print("== 答案候选 ==")
    for h in pkg.answer_candidates:
        print(f"  [{h.case.case_id}] scope:{h.case.scope} 触发:『{h.case.canonical_trigger}』 回复: {h.case.response[:2]}")
    print("== 回应思路(pattern) ==")
    for s in pkg.patterns:
        print(f"  [{s.id}] {s.name} → {s.description}")
    if args.debug:
        st = pkg.state
        print(f"\n[debug] KB_AVAILABLE={st.KB_AVAILABLE} KB_CALLED={st.KB_CALLED} "
              f"KB_SEARCH_MS={st.KB_SEARCH_MS}ms candidates={st.CANDIDATES} "
              f"patterns={st.PATTERN_MATCH} total={st.TOTAL_MS}ms")


if __name__ == "__main__":
    main()
