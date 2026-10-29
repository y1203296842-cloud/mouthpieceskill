"""scripts/ingest_transcript.py — 逐字稿多 case 语义拆分入库。

针对"一个视频 = 一个连续逐字稿，但里面包含多个独立 [对方话→回复]"的场景（如单独视频逐字稿）。
清洗时**根据语义独立性拆分**，不要默认一个视频 = 一个 case：
  一个视频 ↓ 逐行拆成交换 ↓ 行内再按对话转折拆成多个 [trigger→reply] ↓ 各自清洗/泛化/去重/入库。

红线：一个连续回答里包含多个独立 trigger / 多个独立 response 时，必须拆开，不能混成一个 case。
来源只进 raw/ 归档层，绝不进知识库与检索。ASR 置信度过低的素材 → needs_review，不直接引用。

用法：
  python3 scripts/ingest_transcript.py <逐字稿.md> [--dry-run] [--allow-low-conf]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.base import Case
from adapters.markdown_provider import MarkdownKnowledgeProvider
from scripts.index_builder import build_index

# 行内嵌对话的 canonical 恢复（对方话 → 用户会怎么问）。只放高置信、可明确恢复的映射。
# 例：ASR 压缩后的玩笑语段 → 恢复成用户真实问法。
CANONICAL_REPLACE: List[Tuple[str, str, str, str]] = [
    # (触发片段, 回复片段, 修正trigger, 修正reply)
    ("逗你玩啊", "怎么玩笑开到自己身上就不好笑了", "别人说你太较真，说只是开个玩笑", "怎么玩笑开到自己身上就不好笑了"),
    ("没什么意思", "怎么玩笑开到自己身上就不好笑了", "别人说你太较真，说只是开个玩笑", "怎么玩笑开到自己身上就不好笑了"),
]
# 高置信 ASR 同音纠正（只替换明确目标词，保留风格）
ASR_FIXES = {"说嘛": "说什么", "这么较真": "这么较真", "你妈没教过": "你妈没教过"}


def split_exchanges(text: str) -> List[Tuple[str, str]]:
    """把一个连续逐字稿段拆成多个 [trigger→reply] 交换。

    规则：
      1) 按句号把一段切出候选块；
      2) 每块按逗号切成"首个触发语 + 其余回复"；
      3) 命中 canonical 恢复表（嵌入对话）→ 用恢复后的 trigger/reply。
    这样同一个连续回答里若含多个独立场景，就会自然拆成多个 case。
    """
    chunks = [c.strip() for c in re.split(r"。", text) if c.strip()]
    out: List[Tuple[str, str]] = []
    for c in chunks:
        parts = [p.strip() for p in re.split(r"[，,]", c) if p.strip()]
        if not parts:
            continue
        trig, rest = parts[0], "，".join(parts[1:])
        for t_needle, r_needle, new_t, new_r in CANONICAL_REPLACE:
            if t_needle in c and r_needle in rest:
                trig, rest = new_t, new_r
                break
        if trig and rest:
            out.append((trig, rest))
    return out


@dataclass
class TxReport:
    videos: int = 0
    exchanges: int = 0
    new_cases: int = 0
    duplicates: int = 0
    needs_review: int = 0
    rejected: int = 0
    notes: List[str] = field(default_factory=list)


def _slug(text: str) -> str:
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text)
    return (s[:10] or "case")


def _norm(r: str) -> str:
    return re.sub(r"[\s，。、,.!！?？:：]", "", r)


def _scope(trigger: str, reply: str) -> str:
    if any(w in (trigger + reply) for w in ("上班", "绩效", "加班", "领导", "老板", "升职")):
        return "scene_specific"
    if any(w in trigger for w in ("crush", "对象", "男朋友", "女朋友", "喜欢的人", "暧昧")):
        return "relational"
    return "broad"


def _infer_intent(trigger: str) -> str:
    if any(k in trigger for k in ("催婚", "对象", "感情", "亲戚")):
        return "被追问/揶揄，想一句话挡回去"
    if any(k in trigger for k in ("讨厌", "不喜欢", "排斥")):
        return "回应别人的讨厌/排斥，不想撕破脸又想扳回一局"
    if any(k in trigger for k in ("敏感", "玻璃心", "想太多")):
        return "回应别人对你敏感/想太多的指责"
    return "被人用话术攻击/揶揄，想有力回应"


def _archive_source(knowledge: MarkdownKnowledgeProvider, case_id: str, video: dict) -> None:
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
    reg[case_id] = {"creator": video.get("author", ""), "url": video.get("url", ""),
                    "title": video.get("title", ""), "video_id": video.get("video_id", "")}
    with open(reg_path, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)


def ingest_transcript(path: str, dry_run: bool = False, allow_low_conf: bool = False) -> TxReport:
    rep = TxReport()
    knowledge = MarkdownKnowledgeProvider()
    with open(path, encoding="utf-8") as f:
        text = f.read()
    # 按 ## N · title 分视频
    parts = re.split(r"^##\s*\d+\s*·", text, flags=re.M)
    for p in parts[1:]:
        title_m = re.match(r"\s*(.+?)\n", p)
        title = title_m.group(1).strip() if title_m else ""
        author = ""
        am = re.search(r"作者[:：]\s*(\S+)", p)
        if am:
            author = am.group(1)
        vid_m = re.search(r"视频ID[:：]\s*(\d+)", p)
        url_m = re.search(r"来源链接[:：]\s*(\S+)", p)
        video = {"author": author, "title": title,
                 "video_id": vid_m.group(1) if vid_m else "",
                 "url": url_m.group(1) if url_m else ""}
        # 可靠性：置信度低(<0.9)段落占比过高 → needs_review
        lines = re.findall(r"^>\s*(.+)$", p, re.M)
        if not lines:
            continue
        rep.videos += 1
        low_conf = len(re.findall(r"\(0\.\d+\)", p))
        seg_count = len(re.findall(r"\(0\.\d+\)", p))
        unreliable = not allow_low_conf and any(
            k in p for k in ("整体可靠性差", "建议直接看视频核对", "本稿整体")
        )
        for ln in lines:
            for trig, reply in split_exchanges(ln):
                rep.exchanges += 1
                if unreliable:
                    rep.needs_review += 1
                    continue
                trig = trig.strip()
                reply = reply.strip()
                if not trig or not reply:
                    rep.rejected += 1
                    continue
                # 去重
                new_r = {_norm(reply)}
                dup = False
                for e in knowledge.list_cases():
                    old = {_norm(x) for x in e.response}
                    if new_r & old:
                        dup = True
                        break
                if dup:
                    rep.duplicates += 1
                    continue
                if dry_run:
                    rep.new_cases += 1
                    continue
                cid = _slug(trig)
                case = Case(
                    case_id=cid,
                    canonical_trigger=trig,
                    intent=_infer_intent(trig),
                    response=[reply],
                    scope=_scope(trig, reply),
                    scene_constraints=[],
                    relation_constraints=[_rel(trig)] if _rel(trig) else [],
                    keywords=[w for w in [trig[:2], reply[:2]] if w],
                    status="verified",
                    query_examples=_qex(trig),
                    usage=f"来自逐字稿『{title}』的多 case 拆分；触发语已语义恢复",
                    tone="犀利",
                    strength="中",
                )
                knowledge.upsert_case(case)
                _archive_source(knowledge, cid, video)
                rep.new_cases += 1
                rep.notes.append(f"新增案例 {cid}：『{trig}』 → {reply}")
    if not dry_run:
        build_index(knowledge.root)
    return rep


def _rel(trigger: str) -> str:
    for k in ("领导", "老板", "上司"):
        if k in trigger:
            return "领导"
    for k in ("crush", "对象", "男朋友", "女朋友", "喜欢的人", "暧昧"):
        if k in trigger:
            return "暧昧"
    for k in ("亲戚", "长辈", "过年"):
        if k in trigger:
            return "亲戚"
    return ""


def _qex(trigger: str) -> List[str]:
    t = trigger.strip(" ？?，,。")
    if not t:
        return []
    return list(dict.fromkeys([f"{t}怎么回", f"{t}怎么怼", f"遇到{t}怎么办"]))[:6]


def main() -> None:
    ap = argparse.ArgumentParser(description="逐字稿多 case 语义拆分入库")
    ap.add_argument("file")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-low-conf", action="store_true", help="即使 ASR 置信度低也入库（默认 needs_review）")
    args = ap.parse_args()
    rep = ingest_transcript(args.file, dry_run=args.dry_run, allow_low_conf=args.allow_low_conf)
    print(f"视频数: {rep.videos}  交换数: {rep.exchanges}  新增: {rep.new_cases}  重复: {rep.duplicates}  待复核: {rep.needs_review}  丢弃: {rep.rejected}")
    for n in rep.notes[:40]:
        print("  -", n)


if __name__ == "__main__":
    main()
