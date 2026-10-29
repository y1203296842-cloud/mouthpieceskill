"""scripts/ingest.py — 知识注入：解析博主导出（如样例.md）→ 语义清洗 → 去重 → 入库 → 索引。

使用方式：
  python3 scripts/ingest.py <creator.md> [--dry-run]

清洗流程（语义校验版）：
  RAW → 识别素材 → 去噪 → ASR纠错 → 场景/触发语恢复(canonical_trigger) →
  意图(intent) → 拆分回复 → scope/约束 → 失配→needs_review →
  去重(回复重叠≥50%) → 入库(verified) → 来源写入 raw/ 归档 → 自动重建索引。

来源信息（博主名/地址/点赞/链接）只进 raw/ 独立归档层，不进知识库与检索。
去重：回复内容重叠（去标点）≥50% 判重；不重复保存，仅把新例子挂到策略 examples。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.base import Case
from adapters.markdown_provider import MarkdownKnowledgeProvider, project_root
from scripts.cleaner import build_case_from_text
from scripts.index_builder import build_index

# 博主"XX型"标签 → 策略 id（与 knowledge/strategies/ 一致）
TYPE_TO_STRATEGY: Dict[str, str] = {
    "打狗": "reverse-premise", "反杀": "reverse-premise", "暴击": "reverse-premise",
    "威胁": "shift-onto-them", "裁员": "shift-onto-them", "火药": "shift-onto-them",
    "刻薄": "shift-onto-them", "挑衅": "shift-onto-them",
    "差异": "ironic", "阴阳": "ironic", "反讽": "ironic", "羡慕": "ironic",
    "复读": "play-dumb", "装傻": "play-dumb", "健忘": "play-dumb",
    "撩拨": "flirt", "反撩": "flirt", "撩爆": "flirt", "暧昧": "flirt", "撩": "flirt",
    "心疼": "comfort", "体谅": "comfort", "安慰": "comfort",
    "高冷": "aloof", "霸总": "aloof", "客服": "aloof",
    "荒诞": "absurd", "乱套": "absurd", "夸张": "absurd",
}


@dataclass
class VideoRecord:
    title: str
    url: str
    likes: str
    transcript: str
    direction: str = ""
    markers: List[str] = field(default_factory=list)
    video_id: str = ""


@dataclass
class IngestReport:
    total_videos: int = 0
    new_cases: int = 0
    duplicates: int = 0
    noise_discarded: int = 0
    strategies_updated: int = 0
    rejected: int = 0
    needs_review: int = 0
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 解析博主导出
# ---------------------------------------------------------------------------
def parse_creator_md(path: str) -> List[VideoRecord]:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    blocks = re.split(r"^###\s*视频\s*\d+", text, flags=re.M)
    records: List[VideoRecord] = []
    for b in blocks[1:]:
        title = _field(b, "视频标题")
        url = _field(b, "视频链接")
        likes = _field(b, "点赞")
        direction = _field(b, "内容方向")
        markers_raw = _field(b, "结构标记")
        vid = _field(b, "视频 ID") or _field(b, "视频ID")
        # 逐字稿可能是多行 `> 句子`，全部收集（标题往往更准，清洗器会综合标题+逐字稿恢复 trigger）
        trans = ""
        m = re.search(r"####\s*视频逐字稿", b)
        if m:
            tail = b[m.end():]
            quote_lines = re.findall(r"^>\s*(.+)$", tail, re.M)
            if quote_lines:
                trans = " ".join(l.strip() for l in quote_lines if l.strip())
        markers = [x.strip() for x in markers_raw.replace("、", ",").split(",") if x.strip()] if markers_raw else []
        if title or trans:
            records.append(VideoRecord(title=title, url=url, likes=likes,
                                       transcript=trans, direction=direction,
                                       markers=markers, video_id=vid))
    return records


def _field(block: str, key: str) -> str:
    m = re.search(rf"^\*\s*{re.escape(key)}\s*[:：]\s*(.+)$", block, re.M)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# 入库（去重 + 来源归档）
# ---------------------------------------------------------------------------
def ingest_record(rec: VideoRecord, knowledge: MarkdownKnowledgeProvider,
                  report: IngestReport) -> str:
    """返回动作：new | duplicate | rejected | needs_review"""
    if not rec.transcript:
        report.rejected += 1
        return "rejected"

    res = build_case_from_text(rec.transcript, title=rec.title, labels=rec.markers)
    if not res.is_knowledge or not res.cleaned or not res.cleaned.response:
        report.rejected += 1
        return "rejected"

    c = res.cleaned
    report.noise_discarded += res.noise_removed

    if c.status == "needs_review":
        report.needs_review += 1
        report.notes.append(f"标记 needs_review：{c.needs_review_reason}（未入库）")
        return "needs_review"

    # 去重：回复内容重叠（去标点）≥50% 判重
    def _norm(r: str) -> str:
        return re.sub(r"[\s，。、,.!！?？:：]", "", r)

    new_replies = {_norm(x) for x in c.response}
    existing = knowledge.list_cases()
    for e in existing:
        old_replies = {_norm(x) for x in e.response}
        overlap = new_replies & old_replies
        if overlap:
            ratio = len(overlap) / max(1, max(len(new_replies), len(old_replies)))
            if ratio >= 0.5:
                report.duplicates += 1
                _link_strategy_examples(knowledge, e.case_id, rec.markers, report)
                return "duplicate"
        if e.canonical_trigger and (e.canonical_trigger in c.canonical_trigger
                                    or c.canonical_trigger in e.canonical_trigger):
            report.duplicates += 1
            _link_strategy_examples(knowledge, e.case_id, rec.markers, report)
            return "duplicate"

    # 新建案例（来源只进 raw/ 归档层）
    case_id = _slug(c.canonical_trigger or rec.title)
    strategies = list(dict.fromkeys(TYPE_TO_STRATEGY.get(m, "") for m in rec.markers))
    strategies = [s for s in strategies if s]
    query_examples = c.query_examples or _gen_query_examples(c.canonical_trigger)
    case = Case(
        case_id=case_id,
        canonical_trigger=c.canonical_trigger,
        intent=c.intent,
        response=c.response,
        scope=c.scope,
        scene_constraints=c.scene_constraints,
        relation_constraints=c.relation_constraints,
        keywords=c.keywords,
        status="verified",
        query_examples=query_examples,
        usage=f"来自『{rec.title}』的清洗产物；scope={c.scope}；触发语已语义纠正",
        tone=c.tone,
        strength=c.strength,
        strategy_refs=strategies,
    )
    knowledge.upsert_case(case)
    _archive_source(knowledge, case_id, rec)
    report.new_cases += 1
    _link_strategy_examples(knowledge, case_id, rec.markers, report)
    report.notes.append(f"新增案例 {case_id}：触发语『{c.canonical_trigger}』，{len(c.response)} 条回复")
    return "new"


def _gen_query_examples(trigger: str) -> List[str]:
    """把 canonical_trigger 转成用户可能真实输入的问法。"""
    t = trigger.strip(" ？?，,。")
    if not t:
        return []
    examples = []
    for frame in ["{t}怎么回", "{t}怎么怼", "遇到{t}怎么办", "{t}该怎么接"]:
        examples.append(frame.format(t=t))
    return list(dict.fromkeys(examples))


def _slug(text: str) -> str:
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text)
    return (s[:10] or "case")


def _archive_source(knowledge: MarkdownKnowledgeProvider, case_id: str, rec: VideoRecord) -> None:
    """把来源写入 raw/ 独立归档层（不进知识库、不进检索）。"""
    raw_dir = os.path.join(knowledge.root, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    reg_path = os.path.join(raw_dir, "case_sources.json")
    reg: Dict[str, Dict[str, str]] = {}
    if os.path.exists(reg_path):
        try:
            with open(reg_path, encoding="utf-8") as f:
                reg = json.load(f)
        except Exception:
            reg = {}
    reg[case_id] = {
        "creator": rec.title.split("怎么")[0].strip() or "未知",
        "url": rec.url,
        "likes": rec.likes,
        "video_id": rec.video_id,
        "title": rec.title,
    }
    with open(reg_path, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)


def _link_strategy_examples(knowledge: MarkdownKnowledgeProvider, case_id: str,
                            markers: List[str], report: IngestReport) -> None:
    seen = set()
    for m in markers:
        sid = TYPE_TO_STRATEGY.get(m)
        if not sid or sid in seen:
            continue
        seen.add(sid)
        s = knowledge.get_strategy(sid)
        if s:
            if case_id not in s.examples:
                s.examples.append(case_id)
                knowledge.upsert_strategy(s)
                report.strategies_updated += 1


# ---------------------------------------------------------------------------
# 文件级入口
# ---------------------------------------------------------------------------
def ingest_file(path: str, dry_run: bool = False,
                knowledge: Optional[MarkdownKnowledgeProvider] = None) -> IngestReport:
    report = IngestReport()
    knowledge = knowledge or MarkdownKnowledgeProvider()
    records = parse_creator_md(path)
    report.total_videos = len(records)
    for rec in records:
        if dry_run:
            res = build_case_from_text(rec.transcript, title=rec.title, labels=rec.markers)
            if res.is_knowledge and res.cleaned:
                report.noise_discarded += res.noise_removed
                if res.cleaned.status == "needs_review":
                    report.needs_review += 1
                else:
                    report.new_cases += 1
            else:
                report.rejected += 1
            continue
        ingest_record(rec, knowledge, report)
    if not dry_run:
        build_index(knowledge.root)   # 自动重建索引
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="知识注入：解析博主导出 → 语义清洗 → 去重 → 入库 → 索引")
    ap.add_argument("file", help="博主导出 .md 路径")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = ap.parse_args()
    rep = ingest_file(args.file, dry_run=args.dry_run)
    print(f"视频总数: {rep.total_videos}")
    print(f"新增案例: {rep.new_cases}")
    print(f"判定重复: {rep.duplicates}")
    print(f"丢弃噪音片段: {rep.noise_discarded}")
    print(f"标记 needs_review(未入库): {rep.needs_review}")
    print(f"策略 examples 更新: {rep.strategies_updated}")
    print(f"无法识别(丢弃): {rep.rejected}")
    for n in rep.notes[:20]:
        print("  -", n)


if __name__ == "__main__":
    main()
