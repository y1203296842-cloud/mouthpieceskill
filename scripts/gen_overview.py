"""scripts/gen_overview.py — 生成案例总览（人工可读，含通用性重判后的 scope 统计）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collections import Counter
from adapters.markdown_provider import MarkdownKnowledgeProvider

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "..", "嘴替知识库-案例总览.md")
OUT = os.path.abspath(OUT)

kp = MarkdownKnowledgeProvider()
cases = kp.list_cases()
c = Counter(x.scope for x in cases)

lines = [
    "# 嘴替知识库 · 案例总览（v3 通用性重判）",
    "",
    "> 2026-10-04 · 全库逐 case 通用性判断："
    + "，".join(f"{k}={v}" for k, v in sorted(c.items()))
    + f"（共 {len(cases)} 条）",
    "",
]
for x in cases:
    cross = "、".join(q for q in x.query_examples[:2])
    lines.append(f"- **{x.case_id}** `{x.scope}`")
    lines.append(f"  - 触发: {x.canonical_trigger}")
    lines.append(f"  - 意图: {x.intent}")
    lines.append(f"  - 关系限制: {'、'.join(x.relation_constraints) or '—'} | 场景限制: {'、'.join(x.scene_constraints) or '—'}")
    lines.append(f"  - 回复: {x.response[0] if x.response else ''}（{len(x.response)}条）")
    lines.append(f"  - 问法: {cross}")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("overview ->", OUT, "| cases:", len(cases))
