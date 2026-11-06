"""scripts/stance.py — 立场判定（位置 × 下一步）。

解决的问题：同一句话，不同关系下该站的位置不同。
  - 同级的室友让你取快递 → 他在越界，你该在上位，不该"好嘞马上"
  - 领导让你加个活 → 平位，可接但可谈条件
  - 对方只是回了句"收到" → 根本不必回，别硬凑回怼

输出（只供内部路由/生成/质检使用，不进用户可见层）：
  behavior  对方做了什么
  relation  你们的关系
  position  ABOVE / AT / BELOW / CUE / DEPENDS / RARE
  next      DIRECT / CHOOSE / NO_REPLY

设计参照：开源项目 jev-chat/jev-chat-jarvis 的「行为 × 关系 → 位置 + 下一步」场景矩阵框架
（MIT）。矩阵内容为本项目自建中文场景表，见 knowledge/stance/matrix.json。
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATRIX_PATH = os.path.join(ROOT, "knowledge", "stance", "matrix.json")

_MATRIX: Optional[Dict] = None

# 自述理亏的判定：责任词 + 前面 20 字内出现"我"，避免把"同事说我不行"误判成认错场景
_FAULT_WORDS = ("漏", "忘", "搞砸", "迟到", "没做到位", "不到位", "没做好", "做得不好",
                "弄错", "搞错", "拖延", "拖了", "出问题")

# 句式级规则：不靠穷举词表
_ASSIGN_VERB = re.compile(r"(让我|叫你|喊我|要我去|派我|安排我)[^。！？\n]{0,8}(加|做|弄|改|帮|来|干|写|跑|取|送|接|值)")
_WORK_NOUN = ("方案", "报告", "活", "进度", "表格", "数据", "文案", "东西", "项目", "稿")
_NEGATIVE = ("不到位", "没做好", "做得不好", "不行", "有问题", "太差", "拖了")

# 简短确认类：只有输入本身就短、且没有别的冲突行为时，才判 NO_REPLY
_ACK_WORDS = ("收到", "知道了", "好的", "行吧", "嗯", "ok", "1", "k")
_CONFLICT_MARKERS = ("说我", "骂", "怼", "阴阳", "嘲笑", "嫌", "内涵", "使唤", "催", "拒绝",
                     "怎么回", "怎么办", "开涮", "贬低", "攀比")


def _load() -> Dict:
    global _MATRIX
    if _MATRIX is None:
        _MATRIX = json.load(open(MATRIX_PATH, encoding="utf-8"))
    return _MATRIX


def detect_relation(text: str) -> str:
    """归一到矩阵关系键。顺序即优先级：越具体的关系越先判。"""
    m = _load()
    low = text.lower()
    for key in ("ex", "crush", "partner", "senior", "family", "report", "client", "stranger"):
        for k in m["relations"].get(key, []):
            if k.lower() in low:
                return key
    return "peer"


def _structural_behavior(text: str) -> Optional[Dict]:
    """句式级判定：处理词表覆盖不到的常见结构（换词不失灵）。

    ①「让我/叫你/派我 + 动作」= 对方给你派活
    ②「说我 + 工作产出 + 负面词」= 你的活确实有问题（认错场景，而非贬低）
    """
    m = _load()
    if _ASSIGN_VERB.search(text) and not any(w in text for w in ("拒绝", "不想", "怎么推", "婉拒")):
        b = dict(m["behaviors"]["B_ADD_WORK"])
        b["id"] = "B_ADD_WORK"
        return b
    if "说我" in text and any(n in text for n in _WORK_NOUN) and any(v in text for v in _NEGATIVE):
        b = dict(m["behaviors"]["B_OWN_FAULT"])
        b["id"] = "B_OWN_FAULT"
        return b
    return None


def detect_behavior(text: str) -> Dict:
    """按优先级扫描行为关键词。返回命中的行为定义；都不中则返回一般行为。"""
    m = _load()
    beh = _structural_behavior(text)
    if beh:
        return beh
    # 认错场景单列：责任词，且"我"在其前面 20 字内出现
    for w in _FAULT_WORDS:
        i = text.find(w)
        if i >= 0 and "我" in text[max(0, i - 20):i]:
            b = dict(m["behaviors"]["B_OWN_FAULT"])
            b["id"] = "B_OWN_FAULT"
            return b
    for bid in m["priority"]:
        b = m["behaviors"].get(bid)
        if not b:
            continue
        if any(k and k in text for k in b["keywords"]):
            # 简短确认：只在整个输入很短、且没有别的冲突信号时才算
            if bid == "B_SHORT_ACK":
                if len(text) > 24 or any(c in text for c in _CONFLICT_MARKERS):
                    continue
            b = dict(b)
            b["id"] = bid
            return b
    return {"id": "B_GENERIC", "label": "一般对话/冲突", "rows": {}, "next": {}}


def judge(text: str) -> Dict:
    """核心判定：返回 behavior / relation / position / next（+ 一句内部说明）。"""
    m = _load()
    rel = detect_relation(text)
    beh = detect_behavior(text)
    position = beh.get("rows", {}).get(rel, "AT")
    nxt = beh.get("next", {}).get(rel, "CHOOSE")
    # 一般行为：被求回应时按平位给选项；只是陈述则不回
    if beh.get("id") == "B_GENERIC":
        wants = any(k in text for k in ("怎么回", "怎么怼", "怎么办", "回他", "回她", "怼"))
        position, nxt = ("AT", "CHOOSE") if wants else ("AT", "NO_REPLY")
    note = _note(beh.get("id", ""), rel, position, nxt, beh.get("label", ""))
    return {
        "behavior": beh.get("id", "B_GENERIC"),
        "behavior_label": beh.get("label", ""),
        "relation": rel,
        "position": position,
        "position_note": m["positions"].get(position, ""),
        "next": nxt,
        "next_note": m["next"].get(nxt, ""),
        "note": note,
    }


_REL_CN = {"senior": "上级", "peer": "平级", "report": "下级", "client": "客户",
           "stranger": "陌生人", "family": "亲戚/家人", "partner": "恋人", "crush": "暧昧对象",
           "ex": "前任", "unknown": "不明"}


def _note(bid: str, rel: str, position: str, nxt: str, label: str) -> str:
    """给生成层的一句中文约束（机制级，不写死话术）。"""
    if nxt == "NO_REPLY":
        return "这种不用回，或一个字带过；别硬凑回怼。"
    if position == "ABOVE":
        return f"{_REL_CN.get(rel, rel)}做的是「{label}」，你在上位：别自证、别认前提、别替对方找台阶。"
    if position == "AT":
        return f"{_REL_CN.get(rel, rel)}做的是「{label}」，平位：话可以软，事实和边界不让。"
    if position == "BELOW":
        return "确实理亏：认错 + 给确定性，别辩解、别踢皮球。"
    if position == "RARE":
        return f"{_REL_CN.get(rel, rel)}这么说话反常，反常本身就是能接的点。"
    if position == "CUE":
        return "对方在给信号或已设边界：只回应信号，别顺着展开。"
    if position == "DEPENDS":
        return "先看要不要接情绪：对方在示弱就先接住，别急着怼。"
    return ""


def describe() -> str:
    """调试用：打印矩阵规模与覆盖。"""
    m = _load()
    n_beh = len(m["behaviors"])
    n_rel = len(m["relations"])
    return f"stance matrix: {n_beh} behaviors × {n_rel} relations"


def _cli() -> None:
    import sys
    if len(sys.argv) < 2:
        print(describe())
        return
    out = judge(sys.argv[1])
    for k, v in out.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    _cli()
