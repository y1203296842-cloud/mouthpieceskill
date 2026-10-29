"""scripts/capability.py — 嘴替能力路由（多能力分离，互不串台）。

嘴替不是单一功能，而是多个能力：
  RETORT   回怼（现状主打）
  BLESSING 节日祝福 / 祝福话术（后续喂料扩展）
  HIGH_EQ  高情商回复 / 得体话术（后续喂料扩展）
  RELATION 撩 / 情感拉扯（目标模式 G2）
  OTHER    未识别 → 不硬套任何能力

核心约束：不同能力用各自的资料，**绝不跨能力串台**——
该回怼时绝不能写祝福，该祝福时绝不能写回怼。
每个能力有独立的触发信号、独立的生成入口、独立的资料槽位。
```
用法：
  from scripts.capability import detect_capability, capability_generate
  cap = detect_capability(user_input)
  if cap != "RETORT":  # 非回怼走各自能力入口，绝不碰回怼资料
      result = capability_generate(cap, user_input)
```
"""
from __future__ import annotations

import re
from typing import List

# ---------------------------------------------------------------------------
# 能力触发信号（关键词；命中即路由到对应能力）
# ---------------------------------------------------------------------------
BLESSING_SIGNALS = [
    "祝福", "祝福语", "祝你们", "祝大家", "祝你", "贺卡", "拜年", "过年好",
    "新年快乐", "生日快乐", "中秋", "端午", "元宵", "春节祝福", "节日祝福",
    "恭祝", "愿...", "吉言", "贺词", "吉祥话", "婚礼祝福", "满月祝福", "开业大吉",
]
HIGH_EQ_SIGNALS = [
    "高情商", "高情商回复", "情商回复", "说得体", "得体", "不失面子", "不伤和气",
    "委婉", "不失礼", "圆场", "台阶", "给台阶", "情商话术", "体面", "怎么不失礼",
]
RELATION_SIGNALS = [
    "crush", "暧昧", "撩", "调情", "已读不回", "找话", "喜欢的人", "对象",
    "男朋友", "女朋友", "升温", "破冰", "哄", "绝交", "冷淡",
]
# 回怼：默认能力（只要不是明确偏祝福/高情商/撩，都算回怼/回话）


def detect_capability(text: str) -> str:
    """识别用户这次要的是哪个能力。未命中明确信号 → RETORT（现状主打）。"""
    if any(k and k in text for k in BLESSING_SIGNALS):
        return "BLESSING"
    if any(k and k in text for k in HIGH_EQ_SIGNALS):
        return "HIGH_EQ"
    if any(k and k.lower() in text.lower() for k in RELATION_SIGNALS):
        # 撩/情感拉扯单独成能力，避免被当"回怼"或"祝福"
        return "RELATION"
    return "RETORT"


# ---------------------------------------------------------------------------
# 各能力的最小生成骨架（现阶段骨架级：保证"该能力能用、且绝不串台"）
# 祝福/高情商资料后续喂料时替换成知识库驱动。RETORT 走既有招式驱动生成。
# ---------------------------------------------------------------------------

# 通用祝福（不绑定具体节日，可直接用；骨架级）
_GENERIC_BLESSINGS = [
    "愿你把日子过成自己喜欢的模样，顺遂平安，喜乐常有。",
    "愿你新的一年里，想要的都拥有，得不到的都释怀。",
    "愿你被生活温柔以待，所有努力都有回响。",
    "愿你的每一天都值得纪念，每一次出发都顺利抵达。",
]
# 春节/新年
_NEWYEAR_BLESSINGS = [
    "新年快乐！愿你和家人身体健康，日子越过越红火。",
    "新的一年，愿你钱包鼓鼓、心情暖暖，万事皆可期。",
    "春节快乐！愿好运气天天来找你，烦心事统统让路。",
]
# 生日
_BIRTHDAY_BLESSINGS = [
    "生日快乐！愿你眼里有光，心里有糖，岁岁皆欢愉。",
    "又长一岁，愿你依然活得热烈而自由，生日快乐！",
]
# 婚礼
_WEDDING_BLESSINGS = [
    "新婚快乐！愿你们在漫长的岁月里，始终是彼此的笃定。",
    "恭喜你们！愿白头到老，把日子过成诗。",
]

# 高情商"温和化解"骨架（用于不当面撕破脸的场合；骨架级，待喂料扩展）
_HIGH_EQ_PATTERNS = [
    "你这么说，我听着多少有点不是滋味，但我知道你不是故意的。",
    "这件事咱们换个说法聊，可能更好。",
    "我理解你的出发点，但这么说我心里不太舒服。",
    "咱们把话说开，就不伤和气了。",
]

# 撩·暧昧推拉骨架（G2 目标模式；骨架级）
_RELATION_PATTERNS = [
    "你这话里有话啊，我该往哪儿接？",
    "在等你主动来找我。",
    "你说的这个，我可记下了。",
]

_BY_CAP = {
    "BLESSING": {"keyword": "祝福", "pool": _GENERIC_BLESSINGS + _NEWYEAR_BLESSINGS + _BIRTHDAY_BLESSINGS + _WEDDING_BLESSINGS},
    "HIGH_EQ": {"keyword": "高情商", "pool": _HIGH_EQ_PATTERNS},
    "RELATION": {"keyword": "撩", "pool": _RELATION_PATTERNS},
}


def capability_generate(cap: str, user_input: str) -> List[str]:
    """非回怼能力的生成入口。返回可直接使用的回复（绝不混入回怼资料）。"""
    if cap not in _BY_CAP:
        return []
    pool = _BY_CAP[cap]["pool"]
    # 按关键词再精筛：例如提到"生日"优先生日祝福
    picked = []
    for kw in ("生日", "新年", "春节", "结婚", "婚礼", "开业"):
        if kw in user_input:
            for line in pool:
                if kw in line:
                    picked.append(line)
                    break
    if not picked:
        picked = pool[:2]
    return picked


if __name__ == "__main__":
    import sys
    for q in sys.argv[1:] or ["同事说我靠关系怎么回", "给我写个新年祝福", "高情商回复老板催进度", "crush已读不回怎么办"]:
        c = detect_capability(q)
        print(f"[{c}] {q} -> {capability_generate(c, q)}")
