"""scripts/quality_gate.py — 输出前的硬否决质检门。

作用：生成类路由（没有现成高匹配答案时）产出的每一句，先过一遍「不能犯的错」，
不合格的句子不进入最终回答。只删不做风格改写 —— 优秀话术的锋利度不受影响。

五道门（都可单独关闭，默认全开）：
  unsupported_fact  话术替你编了具体金额/日期的事实（你没说过）
  commits_others    话术替你替第三方许了承诺
  opposite_stance   你要拒绝，话术却答应了
  crosses_boundary  对方已划边界，话术还往上凑
  no_reply_overrun  本不必回的场合，话术长篇反打

设计参照：开源项目 jev-chat/jev-chat-jarvis 的候选硬否决清单（MIT）——
unsupported_fact / new_commitment / commits_others / opposite_stance / crosses_boundary。
本文件用纯本地规则实现，不调用任何模型，零依赖。
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

# ---- 事实编造：只拦"第一人称 + 具体数字"这类硬事实，修辞性数字（你第几次）放行 ----
_FACT_PATTERNS = [
    re.compile(r"我[^。！？\n]{0,10}\d+(?:\.\d+)?\s*(?:元|块|万|千)"),          # 我花了300块
    re.compile(r"我[^。！？\n]{0,10}\d{1,2}\s*月\s*\d{1,2}\s*[日号]"),           # 我3月5号就给了
    re.compile(r"我[^。！？\n]{0,10}\d{1,2}\s*(?:点|小时|天|年|个月)[^。！？\n]{0,4}(?:等|花|给了|就)"),
]

# ---- 替第三方承诺 ----
_THIRD_PARTY = ("我同事", "我朋友", "我领导", "我老板", "我们公司", "我们团队", "我们部门", "我们组")
_PROMISE = ("会给", "会做", "搞定", "安排", "负责", "保证", "处理", "解决", "发过去", "送过去", "接手")

# ---- 立场相反 ----
_REFUSE_INTENT = ("拒绝", "怎么推", "推掉", "婉拒", "不想去", "不想帮", "推不掉", "没法拒绝", "怎么拒绝")
_AGREE_OPENER = re.compile(r"^[（(]?(行|好|可以|没问题|成|那就|行吧|OK|ok)[，,。！!]?")
_TRANSITION = ("但", "不过", "前提", "除非", "只是", "先", "得看", "看情况")

# ---- 越边界 ----
_BOUNDARY_SET = ("别再说", "别联系", "不想聊", "别烦我", "拉黑", "别找我", "不要再")
_BOUNDARY_PUSH = ("再聊", "见一面", "再想想", "聊聊", "给我个机会", "等我", "再试")

# ---- 不必回却长篇 ----
_NO_REPLY_MAX_CHARS = 26

# ---- 上位场景自降低位（你明明占理，话术却在认错讨好） ----
_SELF_DOWNGRADE = ("不好意思", "抱歉", "对不起", "是我不对", "我的错", "我道歉",
                   "我反省", "我检讨", "给您添麻烦", "是我不好", "我改还不行")

# ---- 隐性自降：把对方对你的评价当成事实接住（比"抱歉"更隐蔽） ----
_AGREE_PREMISE = ("你说得对", "你说得没错", "你说得有道理", "您说得对", "您说得没错")
_ADMIT_INFERIOR = ("配不上您", "配不上你", "请您多指教", "请您指教", "是我不够好",
                   "我不够格", "我不配", "我这水平确实", "我高攀")
_CONCEDE_TURN = ("但", "不过", "可", "然而", "问题是", "可惜", "除非", "至于")


def _is_generative(route: str) -> bool:
    return route in ("move_generate", "style_generate", "pattern_generate", "capability_generate")


def check_unsupported_fact(reply: str, query: str) -> bool:
    """话术里第一人称的具体金额/日期，用户输入里没有 → 算编事实。"""
    for p in _FACT_PATTERNS:
        m = p.search(reply)
        if m:
            # 用户输入里出现同样数字才算有依据
            digits = re.findall(r"\d+", m.group(0))
            if digits and any(d in query for d in digits):
                continue
            return False
    return True


def check_commits_others(reply: str, query: str) -> bool:
    """替第三方许承诺；"让他自己来"这类甩锅/拒绝放行。"""
    for src in _THIRD_PARTY:
        if src in reply:
            tail = reply[reply.index(src):]
            if any(w in tail[:30] for w in _PROMISE):
                # 甩锅式表达不算承诺
                if any(w in tail[:30] for w in ("自己", "别", "滚", "找他去", "问他自己")):
                    continue
                return False
    return True


def check_opposite_stance(reply: str, query: str) -> bool:
    """你要拒绝，话术却直接答应。"""
    if not any(k in query for k in _REFUSE_INTENT):
        return True
    if _AGREE_OPENER.match(reply.strip()):
        if not any(t in reply for t in _TRANSITION):
            return False
    return True


def check_crosses_boundary(reply: str, query: str) -> bool:
    """对方已设边界，话术还往上凑。"""
    if not any(k in query for k in _BOUNDARY_SET):
        return True
    return not any(w in reply for w in _BOUNDARY_PUSH)


def check_no_reply_overrun(reply: str, query: str, next_action: str) -> bool:
    """本不必回的场合，一句超过 26 字就算过火。"""
    if next_action != "NO_REPLY":
        return True
    return len(reply.strip()) <= _NO_REPLY_MAX_CHARS


def check_self_downgrade(reply: str, query: str, position: str) -> bool:
    """你明明在上位/平位，话术却在认错讨好，或把对方的评价当成事实接住。"""
    if position in ("", "BELOW"):
        return True
    if any(w in reply for w in _SELF_DOWNGRADE):
        return False
    if any(w in reply for w in _ADMIT_INFERIOR):
        return False
    for w in _AGREE_PREMISE:
        if w in reply and not any(t in reply for t in _CONCEDE_TURN):
            return False
    return True


CHECKS = (
    ("unsupported_fact", check_unsupported_fact, "替你编了没说过的事实"),
    ("commits_others", check_commits_others, "替你替别人许了承诺"),
    ("opposite_stance", check_opposite_stance, "你要拒绝它却答应了"),
    ("crosses_boundary", check_crosses_boundary, "对方划了边界你还往上凑"),
)

_DISABLE_ENV = "MOUTHPIECE_GATE_OFF"   # 逗号分隔的检查名；"all" 全关


def _disabled() -> set:
    raw = os.environ.get(_DISABLE_ENV, "").strip()
    if not raw:
        return set()
    if raw == "all":
        return {c[0] for c in CHECKS} | {"no_reply_overrun"}
    return {x.strip() for x in raw.split(",") if x.strip()}


def gate_one(reply: str, query: str, next_action: str = "", position: str = "") -> Tuple[bool, List[str]]:
    """单句质检。返回 (是否放行, 失败原因列表)。"""
    off = _disabled()
    fails: List[str] = []
    for cid, fn, label in CHECKS:
        if cid in off:
            continue
        try:
            ok = fn(reply, query)
        except Exception:
            ok = True   # 质检自身异常绝不拦话术
        if not ok:
            fails.append(f"{cid}:{label}")
    if "no_reply_overrun" not in off:
        try:
            if not check_no_reply_overrun(reply, query, next_action):
                fails.append("no_reply_overrun:本不必回却长篇")
        except Exception:
            pass
    if "self_downgrade" not in off:
        try:
            if not check_self_downgrade(reply, query, position):
                fails.append("self_downgrade:上位场景却认错自降")
        except Exception:
            pass
    return (not fails, fails)


def gate(replies: List[str], query: str, stance: Optional[Dict] = None) -> Tuple[List[str], List[Dict]]:
    """整批质检。返回 (放行的句子, 被拦记录[仅调试用])。"""
    if os.environ.get("MOUTHPIECE_GATE", "on").strip().lower() in ("off", "0", "false", "no"):
        return list(replies), []
    nxt = (stance or {}).get("next", "")
    pos = (stance or {}).get("position", "")
    kept: List[str] = []
    blocked: List[Dict] = []
    for ln in replies:
        ok, fails = gate_one(ln, query, nxt, pos)
        if ok:
            kept.append(ln)
        else:
            blocked.append({"reply": ln, "fails": fails})
    # 本不必回的场合：最多留 1 句，且必须够短（别用三段话回一句"收到"）
    if nxt == "NO_REPLY" and kept and "no_reply_overrun" not in _disabled():
        short = sorted(kept, key=len)
        if len(short) > 1 or len(short[0]) > _NO_REPLY_MAX_CHARS:
            for extra in kept:
                if extra != short[0]:
                    blocked.append({"reply": extra, "fails": ["no_reply_batch:不必回却给了多条"]})
            kept = [short[0]]
    return kept, blocked
