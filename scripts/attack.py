"""scripts/attack.py — 攻击前提隔离引擎（机制级，不依赖"维度枚举"）。

为什么不是"加维度"：
  对方可以拿【任何】特征攻击——脚臭、口臭、脱发、牙黄、穿得土、手机便宜、住得差……
  每加一个维度永远做不完。所以本引擎：
    · 判定攻击：靠【攻击句式】（"说我X / 骂我X / 阴阳我X …"）+【贬义信号】（负面评价抽象层，
      不是维度分类，更不是逐特征补丁）+ 回怼意图词。
    · 识别特征：从句式里提取被攻击的 trait（转述式优先；命令句用贬义信号兜底）。
    · 处理策略：先【隔离攻击前提】（不确认 / 不解释 / 不自证 / 不纠缠），再选反制路径。

事实不确定性（硬原则）：
  对方说"你X" ≠ X 是真的。内部默认 claim_is_verified=unknown，
  绝不替对方确认、绝不把攻击陈述升级成事实、绝不生成隐含确认前提的回复
  （"脚臭能治" / "洗洗就好" / "我X怎么了" / "我就是X" 全部视为确认前提，拒绝）。

生成硬规则（任何策略都必须满足）：
  1. 不隐含确认攻击前提（trait 锚定检查）。
  2. 默认不自嘲认下（"我X我自豪 / 对，我就是X"），除非用户明确要玩梗/自嘲。
  3. 不硬找对称、不歪曲事实（"你也X / 你比我还X"），反击必须建立语境上的有效关系。
  4. 答案互相兼容：不出现"承认/否认"自相矛盾；宁给两个真正成立的，不硬凑三个。
"""
from __future__ import annotations

import re
from typing import Dict, List

# ---------------------------------------------------------------------------
# 一、攻击判定：攻击句式（主判定，不依赖任何特征词）
# ---------------------------------------------------------------------------
# 转述攻击句式：对方说我X/骂我X/阴阳我X … → 命中即攻击（X 是什么不重要）
_ACCUSATION_PHRASES = [
    "说我", "骂我", "阴阳我", "内涵我", "嫌弃我", "嘲笑我", "嫌我", "损我", "挤兑我",
    "瞧不起我", "看不上我", "打击我", "贬低我", "人身攻击我", "拿我", "笑我", "吐槽我", "数落我",
]
# 命令句/贴标签句式："你X" / "你是不是X" / "你就是个X"
_COMMAND_MARKERS = ["你", "你是不是", "你就是个", "你就是", "我看你"]

# ---------------------------------------------------------------------------
# 二、贬义信号（"负面评价"抽象层：帮助命令句判定 + 特征提取；可继续扩充，
#    但判定主靠句式，信号只是启发，绝不是"没命中就处理不了"的门槛）
# ---------------------------------------------------------------------------
_NEGATIVE_SIGNALS = [
    # 直接贬损
    "丑", "难看", "土", "寒碜", "矮", "胖", "瘦", "穷", "老", "笨", "蠢", "差", "菜", "弱",
    "废", "渣", "烂", "廉价", "便宜", "寒酸", "邋遢", "懒", "油腻", "小气", "抠", "挫", "窝囊", "掉价",
    # 体貌特征（示例性，非枚举门槛）
    "脚臭", "口臭", "秃头", "秃", "脱发", "发际线", "黑眼圈", "皮肤差", "牙黄", "手粗",
    "狐臭", "驼背", "头大", "肚子大", "假鞋", "腿短", "眼睛小", "脸大", "脖子短", "头发少",
    # 身份/境遇贬损
    "没对象", "没人要", "嫁不出去", "娶不到", "单身狗", "光棍", "没文化", "学历低",
    "没读过书", "没背景", "底层", "没地位", "打工仔", "没本事", "没出息", "没前途", "没用",
    "工资低", "买不起", "住得差", "穿得土", "品味差", "手机便宜",
    # 能力/品性贬损
    "反应慢", "拖后腿", "情商低", "不会说话", "不合群", "没朋友", "脾气差", "记性差",
    "不靠谱", "自私", "小心眼", "玻璃心", "作", "矫情", "太敏感", "想太多",
]
# 结果类（"你优势却差结果"里的结果；只参与攻击判定，不作为 trait 提取）
_OUTCOME_SIGNALS = ["混成这样", "混得差", "混得不好", "也就这样", "就这", "就这点", "没混出名堂",
                    "工资这么低", "工资低", "住这么差", "住得差", "这么惨", "这么穷", "买不起"]

# ---------------------------------------------------------------------------
# 三、对方"声称占优"的优势身份 + 共享语境 识别（现实反差/拆前提的前提）
# ---------------------------------------------------------------------------
ADVANTAGE_DESCRIPTORS = ["有钱", "学历高", "工作好", "能力好", "条件好", "水平高", "那么厉害", "那么优秀",
                         "长得好", "漂亮", "帅", "高", "瘦", "名校", "重点", "大厂", "海归", "聪明", "能干"]
# 把"这么/那么/真/确实"等修饰词去掉后匹配，否则"学历这么高"匹配不到"学历高"
_ADV_PREFIX = re.compile(r"这么|那么|真|确实|实在|也太|也太")
SHARED_CONTEXT = ["公司", "上班", "同事", "地铁", "公交", "饭局", "一起", "同班", "寝室", "合租", "挤"]
EXAGGERATION = ["太", "极", "真是", "竟然", "居然", "这么", "就这", "也配", "也好意思"]

# 优势身份指"他/她/别人/对象"（如"他长得帅我配不上"）→ 不是"你的优势被攻击"，禁用优势类策略
_THIRD_PARTY = ("他帅", "她帅", "他好", "她好", "别人帅", "别人漂亮", "对象帅", "男朋友帅", "女朋友漂亮",
                "他有钱", "他学历高", "别人有钱", "别人学历高",
                "他长得帅", "她长得漂亮", "别人长得帅", "别人长得漂亮")


def _norm_adv(query: str) -> str:
    return _ADV_PREFIX.sub("", query)


def extract_trait(query: str) -> str:
    """提取被攻击的特征（trait）。
    优先级：转述句式（"说我X/骂我X/嫌我X…"）→ 在截取段里找贬义信号词；
    再退到命令句中的贬义信号词。找不到 → ""（生成用通用反转评价句，不嵌入）。
    结果类（"混成这样"）不作为 trait。"""
    for ph in sorted(_ACCUSATION_PHRASES, key=len, reverse=True):
        i = query.find(ph)
        if i >= 0:
            seg = query[i + len(ph):]
            seg = re.split(r"[怎么回怼办什？?。，,!！\s吗呢要吧啊我你他是打击聊]", seg)[0]
            for w in _NEGATIVE_SIGNALS:
                if w in seg:
                    return w
            # 无信号词命中：取"被攻击内容"前 2 字（如"养猫""学校""单身""做饭"），保证点对点
            head = seg[:2]
            if head and not any(v in head for v in ("的", "了", "是", "也", "就", "还")):
                return head
            return ""
    for w in _NEGATIVE_SIGNALS:
        if w in query and any(m in query for m in _COMMAND_MARKERS):
            return w
    return ""


def analyze_attack(query: str) -> Dict:
    """分析：攻击句式/特征/优势身份/共享语境/夸大/贴标签/结果/第三方优势。"""
    phr = [p for p in _ACCUSATION_PHRASES if p in query]
    neg = [w for w in _NEGATIVE_SIGNALS if w in query]
    cmd = any(m in query for m in _COMMAND_MARKERS)
    intent = any(w in query for w in ("怎么回", "怎么办", "怎么怼", "怼回去", "回击", "反击", "挡回去", "怼他", "怼她"))
    attacked = bool(phr) or (bool(neg) and (cmd or intent))
    trait = extract_trait(query)
    adv = [a for a in ADVANTAGE_DESCRIPTORS if a in _norm_adv(query)]
    shared = [c for c in SHARED_CONTEXT if c in query]
    exagg = any(e in query for e in EXAGGERATION)
    neg_outcome = any(x in query for x in _OUTCOME_SIGNALS)
    labeling = any(x in query for x in ("你就是个", "你就是", "你这种人", "我看你"))
    third_party_adv = any(x in query for x in _THIRD_PARTY)
    return {
        "attacked": attacked,
        "accusation_phrases": phr,
        "trait": trait,
        "dimension": trait,          # 兼容旧字段名：trait 即"被攻击的特征"
        "query": query,
        "advantage": adv,
        "shared_context": shared,
        "exaggerated": exagg,
        "labeling": labeling,
        "neg_outcome": neg_outcome,
        "third_party_advantage": third_party_adv,
        "has_reversal_material": bool(adv and shared) and not third_party_adv,
        "has_advantage_premise": bool(adv) and not third_party_adv,
    }


def is_personal_attack(query: str, rel: str = "") -> bool:
    """判定是否"人身攻击/贬低"类。机制：攻击句式（主）+ 贬义信号+主语/意图（辅）
    + "优势身份却差结果"（"你学历这么高怎么混成这样"也是贬低）。
    不依赖具体特征是否"见过"——脚臭、口臭、脱发、任何新特征都能被句式覆盖。
    关系降级：恋人/crush 的情感表达（"你不爱我了/你敷衍我/你不在乎我"）是感情沟通
    不是攻击，应走"接住/安抚"路径（用户红线：该认真时不许回怼）。除非显式攻击词（骂/怼/阴阳/嫌弃/滚）。"""
    _EXPLICIT_ATTACK = ("骂", "怼", "阴阳", "嫌弃", "恶心", "滚", "嘲讽", "侮辱")
    if rel in ("恋人", "crush") and not any(w in query for w in _EXPLICIT_ATTACK):
        return False
    if any(p in query for p in _ACCUSATION_PHRASES):
        return True
    d = analyze_attack(query)
    return d["attacked"] or d["has_advantage_premise"]


# ---------------------------------------------------------------------------
# 四、策略树（反制路径；不绑定固定模板）
# ---------------------------------------------------------------------------
def _s1_no_premise(d: Dict, rel: str) -> List[str]:
    """不接框架 / 反问评价资格 / 转移焦点。几乎总是可用。
    语感参考知识库："别老研究我怎么进来，抓紧研究自己怎么留下来吧"。"""
    t = d["trait"] if len(d["trait"]) >= 2 else ""
    if t:
        return [
            f"你凭什么拿{t}来说我？",
            f"别老盯着我的{t}，先研究研究你自己那摊子事吧。",
            "你先把你自己活明白，再来给我下定义。",
        ]
    return [
        "你凭什么评价我？",
        "别老盯着我，先研究研究你自己那摊子事吧。",
        "你先把你自己活明白，再来给我下定义。",
    ]


# 通用反转评价句的变体池：trait 为空/单字时不嵌入，按 query 选不同句子，避免所有场景同文
_S2_ALT = [
    "拿别人的事说三道四，你是没什么别的好聊了吗？",
    "一天到晚盯着别人，你自己的日子过明白了吗？",
    "你这么爱操心别人，要不先管好你自己？",
]


def _s9_question_source(d: Dict, rel: str) -> List[str]:
    """质疑信息来源 + 感知回射：不否认也不承认对方说的特征，
    先质问"你怎么得出的"（信息来源），再用"别人都没感觉到"否定普遍性，
    最后把特征源头回射给对方自己的感官（鼻子臭/眼光差/耳朵有问题）。
    语感来源（用户原话语感）："你偷摸趴我脚底下闻了？别人都没闻到就你闻到了，是不是你鼻子臭闻什么都臭。"
    关键：绝不进入"我到底臭不臭"的论证——站在前提上接话就是变相承认。"""
    t = d["trait"]
    if not t:
        return []
    # 气味类：信息来源天然可疑（你怎么闻到的），最适用；按特征点部位更点对点
    if any(w in t for w in ("臭", "味", "脚", "口", "汗", "狐", "体味")):
        if "脚" in t:
            spot = "我脚底下"
        elif "口" in t:
            spot = "我嘴边"
        elif any(w in t for w in ("狐", "汗", "体味")):
            spot = "我身上"
        else:
            spot = "人家身上"
        seeds = [
            f"你偷摸趴{spot}闻了？别人都没闻到就你闻到了。",
            "是不是你鼻子有问题，闻什么都臭？",
            "就你一个人闻到了？那得问问你这鼻子是怎么长的了。",
        ]
    # 声音类：主观感知，质疑耳朵
    elif any(w in t for w in ("声", "嗓", "音")):
        seeds = [
            "别人听着都没事，就你听着难受？你那耳朵得修修了吧。",
            "是不是你耳朵有问题，听什么都觉得难听？",
            "就你一个人听出来了？那你这耳朵还挺特别。",
        ]
    # 主观审美类（丑/土/怪…单字也走）：审美是主观的，"你怎么看出来的"成立
    elif t in ("丑", "土", "怪", "矬", "怂", "low", "廉价") or any(w in t for w in ("难看", "丑", "土", "怪")):
        seeds = [
            "别人都没看出来，就你看出来了？你眼睛是带滤镜的吧？",
            "是不是你眼光有问题，看什么都觉得不行？",
            "就你一个人看出来了？那你眼睛该查查了。",
        ]
    # 客观可见特征（高/矮/胖/瘦/老/穷…）：质疑信息来源不成立，交回其他策略（反问资格等）
    else:
        return []
    return seeds


def _s2_reframe_standard(d: Dict, rel: str) -> List[str]:
    """反转评价标准：攻击对方的"关注点/行为"，不捏造对方的事实。"""
    t = d["trait"] if len(d["trait"]) >= 2 else ""
    # 无实义 trait（动词/虚词，如"离开/这/说/觉得"）嵌入反而别扭 → 用空版
    if t in ("离开", "这", "那", "说", "觉得", "认为", "很", "太", "好", "没", "不"):
        t = ""
    if t:
        return [
            f"你这么在意别人{t}的事，平时应该挺闲的。",
            f"拿{t}说事，你是没什么别的好聊了吗？",
            "别人过成什么样都值得你操心，你日子过得挺没意思的。",
        ]
    seed = sum(ord(c) for c in (d.get("query") or ""))
    alt = _S2_ALT[seed % len(_S2_ALT)]
    return [
        "你这么在意别人，平时应该挺闲的。",
        alt,
        "别人过成什么样都值得你操心，你日子过得挺没意思的。",
    ]


def _s7_refuse_advantage_premise(d: Dict, rel: str) -> List[str]:
    """拒绝"优势必然带来好结果/身份决定一切"的隐含前提。
    不否认优势、不硬说对方差，而是拆解"优势→结果"的必然性 + 反问对方的标准。"""
    if not d["advantage"] or d.get("third_party_advantage"):
        return []
    adv = (d["advantage"] or [""])[0]
    if d["neg_outcome"]:
        # "你学历这么高，怎么混成这样" → 不接"学历高就该混得好"前提
        return [
            f"我{adv}是给我自己用的，不是拿来给你评判结果的。",
            "你是按哪套标准在给我人生打分？",
        ]
    # 单纯用优势身份压人（"我比你学历高"）→ 反问资格 + 反转标准
    return [
        f"你{adv}，是拿来比高低用的？",
        "能力又不是靠写在脸上算出来的。",
    ]


def _s3_real_contrast(d: Dict, rel: str) -> List[str]:
    """制造现实反差：必须同时具备【对方声称的优势身份】+【真实的共享现实场景】。
    否则返回空（语境关联不成立 → 拒绝，不硬造）。"""
    adv = (d["advantage"] or [""])[0]
    ctx = (d["shared_context"] or [""])[0]
    if not (adv and ctx):
        return []
    return [
        f"你这么{adv}，怎么还跟我{ctx}？",
        f"{ctx}都在，你还拿这个压我，也不怕说出去好笑。",
    ]


def _s6_debunk_exaggeration(d: Dict, rel: str) -> List[str]:
    """对方明显夸大 → 拆穿（只拆措辞，不编造对方事实）。"""
    if not d["exaggerated"]:
        return []
    return [
        "你说得这么夸张，是怕别人看不出你在带节奏吗？",
        "就你这种说法，说出去不怕别人笑你夸张？",
    ]


def _s5_pressure_back(d: Dict, rel: str) -> List[str]:
    """反向施压：结合公开/多人语境，让对方意识到继续对他不利。"""
    if not any(w in (d["shared_context"] or []) for w in ("同事", "群", "大家", "公司", "饭局")):
        return []
    return [
        "你这话当着大家说，是想让大家评评谁理亏吗？",
        "继续说，正好让在场的人听听你是怎么评价人的。",
    ]


def _s8_natural_close(d: Dict, rel: str) -> List[str]:
    """无有效反击点 → 自然、简短、有分寸地收尾（不为狠硬造逻辑）。"""
    return [
        "这话我接不住，你继续说你的。",
        "行，这个话题就到这吧。",
    ]


def _s10_sibengfan(d: Dict, rel: str) -> List[str]:
    """私生饭/点破造谣：对方诋毁/背后说坏话/造谣 → 把对方放粉丝低姿态 + 贬义（私生饭）+ 点破造谣。
    用户认可句式（一箭三雕：仰视位+骂人+戳穿）。"""
    q = d["query"]
    if not any(w in q for w in ("造谣", "说坏话", "诋毁", "背后", "污蔑", "传我", "骂我", "泼脏水", "坏话")):
        return []
    return [
        "你这么关心我还造谣，你不会是我的私生饭吧？",
        "你对我这点破事这么上心，是把我当偶像追呢？",
    ]


def _s11_fanjiuzhang(d: Dict, rel: str) -> List[str]:
    """翻旧账/抓重复：对方惯犯（又/老是/经常/每次/上次）重复攻击 → 摆历史证据，阴阳到死。
    点对点：嵌入攻击点 trait（如'做饭/学历/胖'），保持扣住具体攻击。"""
    q = d["query"]
    if not any(w in q for w in ("又", "老是", "经常", "每次", "天天", "上次", "总是", "一直", "反复")):
        return []
    t = d["trait"]
    if t:
        return [
            f"诶，你没有新词吗？你上次说我{t}也是这么说的。",
            f"来来回回就拿{t}说事，你的词库是只存了这一条吗？",
        ]
    return [
        "诶，你没有新词吗？我记得你上次也是这么说的。",
        "来来回回就这一句，你的词库是只存了这一条吗？",
    ]


# 策略按优先级排列。关系约束：领导/长辈 → 弱化施压；陌生/网络 → 可施压。
_STRATEGIES = [
    ("S9_question_source", _s9_question_source),
    ("S10_sibengfan", _s10_sibengfan),
    ("S11_fanjiuzhang", _s11_fanjiuzhang),
    ("S7_refuse_advantage_premise", _s7_refuse_advantage_premise),
    ("S3_real_contrast", _s3_real_contrast),
    ("S2_reframe_standard", _s2_reframe_standard),
    ("S1_no_premise", _s1_no_premise),
    ("S6_debunk_exaggeration", _s6_debunk_exaggeration),
    ("S5_pressure_back", _s5_pressure_back),
    ("S8_natural_close", _s8_natural_close),
]

_POWER_OR_ELDER = ("领导", "老板", "上司", "亲戚", "长辈")


def select_strategies(d: Dict, rel: str) -> List[str]:
    """按策略树选 2-3 个可用策略（带优先级 + 关系约束）。"""
    ordered = _STRATEGIES
    if rel in _POWER_OR_ELDER:
        ordered = [s for s in ordered if s[0] not in ("S5_pressure_back", "S6_debunk_exaggeration")] + \
                  [s for s in ordered if s[0] in ("S5_pressure_back", "S6_debunk_exaggeration")]
    elif rel in ("陌生", "网友", "网络"):
        ordered = [s for s in ordered if s[0] in ("S5_pressure_back", "S6_debunk_exaggeration", "S1_no_premise")] + \
                  [s for s in ordered if s[0] not in ("S5_pressure_back", "S6_debunk_exaggeration", "S1_no_premise")]
    picked: List[str] = []
    for name, fn in ordered:
        lines = fn(d, rel)
        if lines:
            picked.append((name, lines))
        if len(picked) >= 2:
            break
    if not picked:
        picked = [("S8_natural_close", _s8_natural_close(d, rel))]
    return picked


# ---------------------------------------------------------------------------
# 五、硬规则自检：前提隔离 / 不自嘲 / 语境关联 / 答案兼容
# ---------------------------------------------------------------------------
def _has_real_relation(line: str, d: Dict) -> bool:
    """语境关联检查：A(攻击) 与 B(反击) 之间是否成立。
    若反击依赖"对方优势/同维度对称"，必须能回答"为什么成立"，否则淘汰。"""
    if line.startswith(("你", "你这么", "你那么")) and "怎么还" in line:
        return bool(d["advantage"] and d["shared_context"])
    return True


def _not_self_deprecating(line: str, user_wants_selfdep: bool) -> bool:
    """默认不自嘲认下：'对，我就是X' / '我X我自豪' / '我承认' 起头句 → 淘汰。"""
    if user_wants_selfdep:
        return True
    if line.startswith("对") and any(x in line for x in ("我就是", "我承认", "是，我")):
        return False
    if re.match(r"^(我就是|对我就|我.*自豪|我.*骄傲|我承认)", line):
        return False
    return True


_SELF_LOWERING = re.compile(
    r"(我|我自己)(确实|真的|就是|也|可|又|是)?(说不过|说不赢|吵不过|怼不过|比不过|比不上|配不上|不如|输给|输你|争不过|拗不过|讲不过|骂不过)(你|他|她)?"
)


def _no_self_lowering(line: str) -> bool:
    """怼人立场规则（用户铁律，最高优先级之一）：回怼时把自己放高位、把对方放低位。
    禁止任何'我说不过你 / 我配不上 / 我不如你 / 我比不过'式自降表达——
    这类句子等于把攻击资格主动让给对方。"""
    return not _SELF_LOWERING.search(line)


def _not_confirm_premise(line: str, trait: str, user_wants_selfdep: bool) -> bool:
    """最终语义检查：回复不得【隐含确认攻击前提】。
    覆盖"X能治 / X洗洗就好 / 我X怎么了 / 我X也没 / 我就是X / 承认我X"整类，
    而不是只拦某一两个具体词。用户显式要自嘲/玩梗时放行。"""
    if user_wants_selfdep:
        return True
    if trait:
        confirms = [
            f"{trait}能治", f"{trait}能改", f"{trait}能修", f"{trait}洗洗", f"{trait}治治",
            f"{trait}改改", f"{trait}好治", f"{trait}又不是", f"{trait}也没什么",
            f"我{trait}怎么", f"我{trait}也没", f"至少我{trait}", f"对，我就是{trait}",
            f"我就是{trait}", f"承认我{trait}", f"我{trait}我自豪", f"我{trait}我骄傲", f"我{trait}就是",
        ]
        if any(c in line for c in confirms):
            return False
    # 无 trait 锚的泛认下（主语指向用户，语义是"接受/治疗/认账"）
    if re.search(r"^(我|这)(的)?(情况|毛病|问题|样)?(能治|能改|洗洗|治治|改改|就这样|确实配不上|多担待)", line):
        return False
    return True


def _kb_direct_safe(line: str, trait: str) -> bool:
    """知识库【直引保护】：贬低型提问（trait 非空）直引现成回答时，
    句子若把攻击特征当作既成事实【复述确认】，即变相认下前提 → 拦下。
    放行条件：含回射词（把矛头转回对方感官/资格）或特征前有否定词（不/没/才）。
    语感依据（用户原话）："拿脚臭说事"= 站在脚臭前提上接话，仍是认下；
    "不敏感还不知道你这么坏"（否定前置）与"是不是你鼻子臭，闻什么都臭"（回射）则不算。
    """
    if not trait or trait not in line:
        return True
    if any(w in line for w in ("鼻子", "眼睛", "耳朵", "眼光", "滤镜", "闻", "听", "看")):
        return True
    m = re.search(re.escape(trait), line)
    if m and m.start() > 0 and line[m.start() - 1] in "不没才别":
        return True
    return False


def _not_contradict(group: List[str]) -> List[str]:
    """答案兼容：去重（默认都不承认事实，天然不矛盾）。"""
    seen, out = set(), []
    for ln in group:
        if ln not in seen:
            seen.add(ln)
            out.append(ln)
    return out


# ---------------------------------------------------------------------------
# 六、对外入口
# ---------------------------------------------------------------------------
def attack_generate(query: str, rel: str = "", user_wants_selfdep: bool = False) -> List[str]:
    """人身攻击/贬低类回怼生成：攻击分析 → 隔离前提 → 选策略 → 生成 → 硬规则检查 → 输出。"""
    d = analyze_attack(query)
    trait = d["trait"]
    if rel == "crush" or any(x in query for x in ("开玩笑", "玩梗", "自嘲", "幽默化解", "不想怼")):
        user_wants_selfdep = bool(any(x in query for x in ("自嘲", "玩梗", "幽默化解", "开玩笑")))
    picked = select_strategies(d, rel)
    out: List[str] = []
    for name, lines in picked:
        for ln in lines:
            if not _has_real_relation(ln, d):
                continue
            if not _not_self_deprecating(ln, user_wants_selfdep):
                continue
            if not _no_self_lowering(ln):
                continue
            if not _not_confirm_premise(ln, trait, user_wants_selfdep):
                continue
            out.append(ln)
        if len(out) >= 2:
            break
    out = _not_contradict(out)
    base = out[:3] or _s8_natural_close(d, rel)[:2]
    # 关系适配句（用户偏好落地，垫底位，不替代主反击）：
    # - 前任/旧情人：追加更狠句（用户偏好：前任场景放开强度，阴阳到对方接不住）
    # - 领导/老板/上司/上级：追加"解决问题型"候选（认错→复盘→给方案；细节按用户实际处境，不编造）
    # - 亲戚/长辈：追加"软阴阳"候选（不撕破脸，把关心反送回去）
    if rel in _POWER_OR_ELDER:
        if rel in ("领导", "老板", "上司", "上级"):
            base.append("这个确实没做到位，我复盘下原因，明天给您个解决方案。")
        else:
            base.append("您比当事人还上心，回头我让他们好好谢谢您。")
    elif rel == "前任":
        base.append("我离开你行不行不知道，你离开我之后这嘴，倒是越来越会酸了。")
    return base


if __name__ == "__main__":
    import sys
    for q in sys.argv[1:] or ["他骂我脚臭怎么回", "你脚臭。", "你口臭。", "你秃头。", "你学历这么高，怎么混成这样？", "他长得帅我配不上怎么办"]:
        print(f"Q: {q}  攻击判定={is_personal_attack(q)}")
        a = analyze_attack(q)
        print("  分析:", a)
        for ln in attack_generate(q):
            print("   -", ln)
        print()
