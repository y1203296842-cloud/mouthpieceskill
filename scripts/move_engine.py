"""scripts/move_engine.py — 回怼招式驱动生成引擎（凌晨方案的核心落地）。

作用：当知识库没有高/中匹配的现成答案时，不再退化成"普通 AI 风格生成"，
而是真正按招式库驱动：
  冲突类型 → 推荐 2-3 个招式 → 用该招式的可迁移骨架实例化成候选回复 → 自检 → 输出。

每个招式内置一组"可迁移、非客套、带招法"的模板行（move-flavored seed）。
同一冲突类型会命中同一组招式，但**不同冲突/不同关系命中不同招式**，所以输出随场景变化，
不会像 style_generate 那样对完全不同的两个问题给出同一句话。

用法：
  from scripts.move_engine import move_generate
  replies = move_generate(query, analysis)   # -> List[str]，用户可直接发送
"""
from __future__ import annotations

import os
import re
import sys
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.plan import detect_conflict_type, _relation  # 复用冲突/关系识别
from scripts.attack import attack_generate, is_personal_attack  # 人身攻击 → 分析+策略树+语境关联

# 每个招式的可迁移模板行（move-flavored；短、有刺、不劝架、跨场景可用）
_MOVE_REPLIES = {
    "m1": ["先别急着给我下定义，你倒是把话说清楚。", "你这么想，是你的事；我不打算按你设定的来。"],
    "m2": ["这话你自己站得住脚吗？", "你说的这个，是不是得先问问我同不同意？"],
    "m3": ["你要不是心里有鬼，不会这么急着贴标签。", "你这么说，是被我说中了吧？"],
    "m4": ["你说得对，我这水平确实配不上您这种“专业”。", "是啊，还得请您多指教，免得我误了您的“标准”。"],
    "m5": ["你这么一说，我差点以为自己在拍什么离谱短剧。", "行，那我下次得安排个剧本跟你对一下。"],
    "m6": ["嗯？你说什么？我好像没听懂。", "这个话题，我先跳过。"],
    "m7": ["行，这个话题就到这吧。", "你说完了？那我先忙。"],
    "m8": ["这个不归我做，你找对人再说。", "我的事，我自己安排。"],
    "m9": ["是啊，我这点确实比不了你。", "我这人就这样，你多担待。"],
    "g1": ["累了吧，先歇会儿，我陪你。", "别硬扛，跟我说说。"],
    "g2": ["你这么说，我该往哪儿接？", "等你主动来找我，我再告诉你。"],
}

# 目标模式招式（G1/G2）与回怼招式混用时，优先保留招式而非模式行。
# m8（拒绝/划界）优先级上调：被使唤/索求场景先给拒绝话术，再给反问。
_MOVE_ORDER_PREF = ["m8", "m4", "m6", "m7", "m5", "m2", "m1", "m3", "m9"]

# AI 劝架腔 / 内部实现词（命中即从候选剔除，保证用户只看到话术）
_FORBIDDEN = ("我理解", "你可以尝试", "从沟通角度", "建议你", "心平气和",
              "每个人都有自己的看法", "希望我的回答", "希望能够帮助到你",
              "知识库", "case", "scope", "版本", "来源", "博主", "检索", "fallback",
              "引擎", "维度", "规则库", "规则", "策略", "招式", "冲突类型",
              "provider", "embedding", "score", "KB_AVAILABLE", "v1.", "工作流", "内部", "索引")


def _self_check(reply: str) -> bool:
    """自检：短、非空、无 AI 劝架腔、无内部实现词、不隐含认下前提（无 trait 锚的泛认下）。"""
    if not reply or len(reply) > 40:
        return False
    if any(w in reply for w in _FORBIDDEN):
        return False
    if re.search(r"^(我|这)(的)?(情况|毛病|问题|样)?(能治|能改|洗洗|治治|改改|就这样|确实配不上|多担待)", reply):
        return False
    return True


def move_generate(query: str, analysis=None, strength_cap: int = 5) -> List[str]:
    """按招式库驱动生成 2-3 条可发送回复。
    人身攻击/贬低类 → 走攻击分析+策略树（不接框架/反转评价/现实反差/拒绝优势前提），
    非人身攻击 → 走通用招式（反问/装傻/冷处理等）。"""
    rel = _relation(query)
    # 恋人情感话题（"你不爱我了/你敷衍我"）→ 接住路径：认错+给确定性，绝不回怼（用户红线）
    if rel == "恋人" and any(w in query for w in ("不爱", "敷衍", "不在乎", "冷淡", "不陪", "没时间", "变心", "躲我")):
        return ["是我疏忽了，我改，行吗？",
                "没有不爱你，是我最近事多，没顾上你。",
                "对不起，让你难受了。我心里一直有你，就是嘴笨，没说好。"]
    # 人身攻击/贬低 → 走策略树引擎（含语境关联 + 不自嘲 + 不硬找对称检查）
    if is_personal_attack(query, rel):
        return attack_generate(query, rel)

    conflict = detect_conflict_type(query)
    # 推荐招式：优先回怼招式（目标模式只在无合适回怼时兜底）
    from scripts.plan import plan as _plan
    p = _plan(query)
    mids = [m["id"] for m in p["recommended_moves"]]
    if strength_cap and p.get("strength_cap"):
        strength_cap = p.get("strength_cap")
    # 排序：回怼招式优先，目标模式放后
    mids.sort(key=lambda m: _MOVE_ORDER_PREF.index(m) if m in _MOVE_ORDER_PREF else 99)

    out: List[str] = []
    seen = set()
    for mid in mids:
        for line in _MOVE_REPLIES.get(mid, []):
            if line not in seen and _self_check(line):
                seen.add(line)
                out.append(line)
        if len(out) >= 3:
            break
    # 兜底：仍空则给一句全局"不接前提"种子，绝不给空
    if not out:
        out = ["先别急着给我下定义，你倒是把话说清楚。"]
    return out[:3]


if __name__ == "__main__":
    import sys as _sys
    for q in _sys.argv[1:] or ["同事说你怎么这么笨怎么回", "亲戚说我怎么还没对象怎么回"]:
        print(f"[{detect_conflict_type(q)}|{_relation(q)}] {q}")
        for r in move_generate(q):
            print("   -", r)
        print()
