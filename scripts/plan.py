"""scripts/plan.py — 回怼招式规划：未知场景的生成支撑。

作用：当知识库没有高匹配答案时，不再"普通 AI 自由发挥"，而是：
  用户输入 → 识别能力 → 识别冲突类型 → 识别关系/场合 → 推荐 2-3 个招式 → 给出每个招式的生成骨架。

这样生成仍"有方法可循"：招式结构 + 用户语气/强度 → 现场生成，而不是退化成客套话。

用法：
  python3 scripts/plan.py "<用户问题>" [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.capability import detect_capability

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOVES_DIR = os.path.join(ROOT, "knowledge", "moves")

# 冲突类型 → 招式（路由表；与 knowledge/moves/ 的 conflict_tags 对应）
_CONFLICT_PATTERNS: List[Tuple[str, str, List[str]]] = [
    # (冲突类型, 触发关键词, 推荐招式)
    ("被质疑能力/身份/人品", "笨,不行,能力,水平,学历,亏你还是,就这,这也算,靠关系,关系户,走后门,拍马屁,升职", ["m1", "m2", "m3"]),
    ("被贴标签", "太敏感,想太多,玻璃心,急了,破防,爱表现,不合群,矫情,作", ["m1", "m3"]),
    ("被阴阳/话里有话", "阴阳,内涵,话里有话,含沙射影,影射,指桑骂槐", ["m6", "m4", "m2"]),
    ("被使唤/越界/被索求", "使唤,拖地,跑腿,借,帮忙,命令,必须,加班,打听,私事,工资,取快递,取件,带饭,打印,跑前跑后,让我,替我,帮我,拒绝,怎么推,怎么婉拒,不想去,不想帮,推不掉,没法拒绝,一直找我", ["m8", "m1", "m6"]),
    ("被当众开涮/恶劣玩笑", "开玩笑,爸妈,你妈,你爸,绝交,丑,胖,矮,土,嘲笑,开涮,骂", ["m5", "m4", "m6"]),
    ("被纠缠/恶意骚扰", "讨厌,烦,骚扰,纠缠,杠,骂我,你懂个屁,别走", ["m7", "m6"]),
    ("被甩锅/赖账", "一出问题,都是你,怪你,赖你,克他,针对,你自己想办法,你来做,你来解决,甩锅,推给你", ["m1", "m2", "m3"]),
    ("被冷落/排除", "跳过,请客,冷落,排除,不带我,不叫我", ["m1", "m3", "m5"]),
    ("被攀比/贬低", "比你贵,攀比,档次,买不起,穷,带饭,外卖,没家", ["m4", "m2", "m5"]),
    ("对方示弱/求助", "好累,难过,伤心,失恋,累,低落,烦", ["g1"]),
    ("暧昧破冰/试探", "crush,暧昧,已读不回,找话,在干嘛,撩,躲我,冷淡,升温", ["g2"]),
]

# 关系提示词 → 强度/语气约束
_RELATION_HINT = [
    ("领导", {"cap_strength": 4, "prefer_moves": ["m4", "m6", "m2"], "note": "对上位者别硬刚，话软立场不退，不留把柄"}),
    ("亲戚", {"cap_strength": 4, "prefer_moves": ["m4", "m6", "m1"], "note": "长辈点到为止，玩笑式带过"}),
    ("陌生人", {"cap_strength": 5, "prefer_moves": ["m7", "m6", "m5"], "note": "陌生场合可强硬/冷处理，不必留面子"}),
    ("crush", {"cap_strength": 2, "prefer_moves": ["g2", "g1", "m9"], "note": "暧昧以撩/接情绪为主，不伤人"}),
    ("恋人", {"cap_strength": 3, "prefer_moves": ["g1", "m9", "g2"], "note": "亲密关系先接情绪/轻调侃，别上强度"}),
    # 前任/旧关系：用户偏好——不再迁就，可冷可反杀，不给旧账留余地
    ("前任", {"cap_strength": 5, "prefer_moves": ["m7", "m5", "g2"], "note": "前任场景放开强度：可冷处理可反杀，别接旧账、别自证"}),
]


def detect_conflict_type(text: str) -> str:
    for name, kws, _ in _CONFLICT_PATTERNS:
        if any(k and k in text for k in kws.split(",")):
            return name
    return "一般回怼"


def _relation(text: str) -> str:
    for k in ("亲戚", "长辈", "过年"):
        if k in text:
            return "亲戚"
    for k in ("领导", "老板", "上司", "上级"):
        if k in text:
            return "领导"
    for k in ("陌生", "大爷", "路人", "网友", "地铁"):
        if k in text:
            return "陌生人"
    for k in ("crush", "暧昧", "喜欢的人"):
        if k in text:
            return "crush"
    # 前任/旧情人：攻击强度放开（用户偏好：前任场景可以更狠）
    for k in ("前任", "前女友", "前男友", "旧情人", "前对象", "前夫", "前妻", "前任说"):
        if k in text:
            return "前任"
    # "对象"要区分：有对象（恋人） vs 没对象/找对象（单身话题，不是恋人关系）
    if any(k in text for k in ("男朋友", "女朋友", "恋人", "对象说", "我对象", "对象嫌", "对象跟", "对象一直", "对象老")):
        return "恋人"
    return "平级"


def _load_move(mid: str) -> Dict:
    import glob
    paths = glob.glob(os.path.join(MOVES_DIR, f"{mid}-*.md"))
    if not paths:
        return {}
    text = open(paths[0], encoding="utf-8").read()
    fm = re.search(r"^---\n(.*?)\n---", text, re.S)
    meta: Dict = {}
    if fm:
        for line in fm.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    # 取"核心结构"段落
    core = re.search(r"## 核心结构（LLM 生成步骤）\n(.*?)(?=## )", text, re.S)
    skeleton = core.group(1).strip() if core else ""
    return {"id": mid, "name": meta.get("name", mid), "conflict_tags": meta.get("conflict_tags", ""),
            "tones": meta.get("tones", ""), "strength_min": meta.get("strength_min", "1"),
            "strength_max": meta.get("strength_max", "5"), "skeleton": skeleton}


def plan(user_input: str) -> Dict:
    """返回能力 + 冲突类型 + 关系约束 + 推荐招式（含生成骨架）。"""
    cap = detect_capability(user_input)
    conflict = detect_conflict_type(user_input)
    rel = _relation(user_input)
    # 推荐招式：冲突类型 → 招式
    moves: List[str] = []
    for name, kws, mids in _CONFLICT_PATTERNS:
        if name == conflict:
            moves = list(mids)
            break
    if not moves:
        moves = ["m1", "m2"]
    # 关系约束：限定强度上限、偏好招式（偏好=加权排序，不硬过滤到只剩 1 个）
    hint = {}
    for rname, h in _RELATION_HINT:
        if rel == rname:
            hint = h
            if h.get("prefer_moves"):
                moves = list(dict.fromkeys(h["prefer_moves"] + moves))
            break
    moves = moves[:3]
    return {
        "query": user_input,
        "capability": cap,
        "conflict_type": conflict,
        "relation": rel,
        "relation_note": hint.get("note", ""),
        "strength_cap": hint.get("cap_strength", 5),
        "recommended_moves": [m for m in (move_specs(mid) for mid in moves)],
    }


def move_specs(mid: str) -> Dict:
    m = _load_move(mid)
    return {"id": mid, "name": m.get("name"), "tones": m.get("tones"),
            "strength": f"{m.get('strength_min')}~{m.get('strength_max')}", "skeleton": m.get("skeleton")}


def main() -> None:
    ap = argparse.ArgumentParser(description="回怼招式规划")
    ap.add_argument("input")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    p = plan(args.input)
    if args.json:
        print(json.dumps(p, ensure_ascii=False, indent=2))
        return
    print(f"能力={p['capability']}  冲突={p['conflict_type']}  关系={p['relation']}  强度上限={p['strength_cap']}")
    if p["relation_note"]:
        print(f"关系提示：{p['relation_note']}")
    for m in p["recommended_moves"]:
        print(f"\n[{m['id']} {m['name']}] 语气={m['tones']} 强度={m['strength']}")
        print(m["skeleton"])


if __name__ == "__main__":
    main()
