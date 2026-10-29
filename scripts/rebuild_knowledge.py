"""scripts/rebuild_knowledge.py — 对现有知识库执行一次"语义重清洗"。

用途：把 v1 的 26 条"原始案例"升级为 v2 的"可复用话术知识"：
  - 新增 canonical_trigger（语义纠正）/ intent / scope / constraints / query_examples / usage
  - response 保留原始表达风格，不规范化
  - 合并近似变体（讨厌你×3 / 太敏感×2 / 关系户×2 / 卡点×2 → 各 1 案）
  - 来源（博主名/地址/点赞）迁入 raw/ 独立归档层，不进知识库与检索
  - 旧 case 整体移入 raw/cases_v1/ 存档（非破坏）
  - 末尾自动重建索引

运行：python3 scripts/rebuild_knowledge.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.base import Case
from adapters.markdown_provider import MarkdownKnowledgeProvider, project_root
from scripts.index_builder import build_index

ROOT = project_root()


def _case(case_id, trigger, intent, scope, scene_constraints, relation_constraints,
          keywords, query_examples, usage, tone, strength, strategies):
    return Case(
        case_id=case_id, canonical_trigger=trigger, intent=intent, response=[],
        scope=scope, scene_constraints=list(scene_constraints),
        relation_constraints=list(relation_constraints),
        keywords=keywords, status="verified", query_examples=query_examples,
        usage=usage, tone=tone, strength=strength, strategy_refs=strategies,
    )


def _with_rel(c, rels):
    c.relation_constraints = list(rels)
    return c


# 每个新 case 对应哪些旧 case id（用于来源归档）
SRC = {
    "reject-tuoyanni": ["workplace-taoyanni-a-010", "workplace-taoyanni-b-021", "workplace-taoyanni-c-025"],
    "taimingan-太敏感": ["workplace-taimingan-a-008", "workplace-taimingan-b-016"],
    "guanxi-靠关系": ["workplace-guanyuanhu-002", "workplace-neihan-014"],
    "kadian-卡点下班": ["workplace-kadianxiaban-011", "workplace-makadian-023"],
    "buxihuanni-领导不喜欢": ["workplace-buxihuanni-004"],
    "rangzuo-让座": ["social-rangzuo-003"],
    "shengao-喜欢高的": ["social-xihuangaode-005"],
    "yidubuhui-已读不回": ["relationships-yidubuhui-006"],
    "zhaohua-找话": ["relationships-zhaohua-009"],
    "haolei-说好累": ["relationships-haolei-012"],
    "chicu-吃醋": ["relationships-chicu-017"],
    "hong-哄冷淡对象": ["relationships-hongcheng-019"],
    "fenshou-说分手": ["relationships-fenshou-001"],
    "buhequn-不合群": ["workplace-buhequn-013"],
    "nijile-你急了": ["workplace-nijile-015"],
    "tuodi-使唤你干活": ["workplace-tuodi-018"],
    "bingui-比你贵": ["workplace-bingui-022"],
    "daxuesheng-亏你还是大学生": ["workplace-daxuesheng-024"],
    "cuihun-亲戚催婚": ["family-qinqiguanxin-020"],
    "paimapi-升职靠拍马屁": ["职场-同事说你升职是拍马屁-026"],
    "huotuichang-喂狗": ["workplace-huotuichang-007"],
}

# 旧 case → 来源（溯源用，只进 raw/ 独立归档层；发布包不含 raw/ 时该映射为空，不影响建库）
def _load_old_src():
    import json as _json
    p = os.path.join(ROOT, "raw", "creators", "source_map.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as _f:
            return _json.load(_f)
    except (_json.JSONDecodeError, OSError):
        return {}

OLD_SRC = _load_old_src()


def build_cases() -> list:
    C = []
    # 1 讨厌
    C.append(_case(
        "reject-tuoyanni", "别人/同事说你很讨厌、非常讨厌你，想一句话怼回去",
        "回应别人的讨厌/排斥，不撕破脸又想扳回一局", "broad", [], [],
        ["讨厌", "非常讨厌", "排斥", "不喜欢", "烦"],
        ["同事说讨厌我怎么回", "室友说我讨厌我怎么办", "别人说很讨厌我", "朋友说烦我怎么怼", "说我讨人厌怎么接"],
        "很通用：无论同事/朋友/室友说讨厌你都能用。选冷淡/装傻/反撩的力度即可。", "冷", "中",
        ["aloof", "play-dumb", "ironic", "reverse-premise", "flirt"]))
    # 2 太敏感（验收 A 的用例）
    C.append(_case(
        "taimingan-太敏感", "别人说你太敏感、想太多、玻璃心，想一句话怼回去",
        "回应别人对你敏感/想太多的指责", "broad", [], [],
        ["太敏感", "敏感", "想太多", "玻璃心", "想多了"],
        ["室友说我太敏感怎么回", "同事说我太敏感", "朋友说我太敏感了怎么怼", "别人说我想太多怎么回", "说我玻璃心怎么接"],
        "核心是反打回：谁都可以说你敏感（室友/同事/朋友/家人）。不接前提+反向施压即可。", "冷", "中",
        ["reverse-premise", "ironic", "shift-onto-them"]))
    # 3 靠关系
    C.append(_case(
        "guanxi-靠关系", "别人内涵/说你靠关系、关系户、走后门",
        "回应别人对你靠关系/走后门的质疑", "broad", [], [],
        ["关系户", "靠关系", "走后门", "关系", "考进来"],
        ["同事说我是关系户怎么回", "别人内涵我靠关系", "说我走后门怎么怼", "领导说我靠关系怎么办",
         "室友内涵我靠关系怎么回", "亲戚说我走后门怎么办"],
        "职场最常见，但'靠关系'的质疑不限于职场；不解释+反打回即可。", "犀利", "高",
        ["reverse-premise", "shift-onto-them", "play-dumb"]))
    # 4 卡点下班（领域限定）
    C.append(_case(
        "kadian-卡点下班", "同事说你卡点下班、踩点下班，阴阳你",
        "回应别人对你卡点下班的阴阳", "domain_specific", ["职场"], [],
        ["卡点", "下班", "摸鱼", "加班"],
        ["同事说我卡点下班怎么回", "同事说我下班太准时怎么怼"],
        "职场限定；若在非职场场景说'下班'不合适，勿跨用。", "犀利", "中",
        ["reverse-premise", "shift-onto-them", "ironic"]))
    # 5 领导不喜欢
    C.append(_with_rel(_case(
        "buxihuanni-领导不喜欢", "领导/上司说他不想用你、不喜欢你",
        "应对领导表达不喜欢的试探，反杀又不失分寸", "relational", [], [],
        ["领导", "不喜欢", "不喜欢你", "不待见", "看不上", "嫌弃"],
        ["领导说不喜欢我怎么回", "老板说不喜欢我怎么办", "上司说我不好怎么接", "领导不待见我怎么办", "领导看不上我怎么回"],
        "适用于领导/上司语境；别用于平级或朋友。", "冷", "中",
        ["reverse-premise", "ironic"]), ["领导"]))
    # 6 让座
    C.append(_with_rel(_case(
        "rangzuo-让座", "陌生人/长辈指责你没让座、没家教",
        "回应陌生人对让座/素质的指责，不卑不亢", "relational", [], [],
        ["让座", "大爷", "地铁", "没家教", "素质"],
        ["大爷说我妈没教让座怎么回", "陌生人指责我没让座怎么怼"],
        "适用于陌生人/长辈当众指责；反打回+荒诞化解。", "犀利", "高",
        ["reverse-premise", "absurd"]), ["陌生人", "长辈"]))
    # 7 喜欢高的
    C.append(_case(
        "shengao-喜欢高的", "自己说'喜欢高的'，被人嘲笑/围攻身高",
        "自嘲接梗，化解被围攻身高", "broad", [], [],
        ["喜欢高的", "身高", "矮", "被喷"],
        ["我说喜欢高的被喷怎么回", "别人笑我矮怎么接", "朋友拿我身高开玩笑怎么怼", "室友说我矮怎么回"],
        "通用自嘲梗，关系不限。", "轻松", "中",
        ["absurd"]))
    # 8 已读不回
    C.append(_case(
        "yidubuhui-已读不回", "crush/喜欢的人已读不回",
        "打破冷场/试探对方，拉近距离", "domain_specific", ["情感"], [],
        ["已读不回", "crush", "暧昧", "不回"],
        ["crush已读不回了怎么找话", "喜欢的人不回消息怎么撩"],
        "情感场景；别用于工作消息已读不回。", "撩", "中",
        ["flirt", "absurd"]))
    # 9 找话
    C.append(_case(
        "zhaohua-找话", "crush/喜欢的人不说话、冷场",
        "破冰找话，拉近距离", "domain_specific", ["情感"], [],
        ["crush", "不说话", "找话", "破冰", "冷场"],
        ["crush不说话怎么找话", "喜欢的人不主动怎么破冰"],
        "情感场景。", "撩", "中",
        ["flirt"]))
    # 10 说好累
    C.append(_case(
        "haolei-说好累", "对象/crush 说好累、很累",
        "心疼+撩，接住对方的累", "domain_specific", ["情感"], [],
        ["好累", "累", "crush", "安慰"],
        ["对象说好累怎么回", "crush说累怎么安慰撩"],
        "情感场景；接住对方的累，先共情再撩。", "心疼", "中",
        ["comfort", "flirt"]))
    # 11 吃醋
    C.append(_case(
        "chicu-吃醋", "crush/喜欢的人故意让你吃醋",
        "回应吃醋试探，撩回去", "domain_specific", ["情感"], [],
        ["吃醋", "crush", "暧昧"],
        ["对象想让我吃醋怎么回", "crush故意气我吃醋怎么办"],
        "情感场景。", "撩", "中",
        ["flirt", "absurd"]))
    # 12 哄冷淡对象
    C.append(_case(
        "hong-哄冷淡对象", "对象/暧昧对象冷淡了，想哄回来",
        "破冰哄回冷淡对象", "domain_specific", ["情感"], [],
        ["冷淡", "crush", "哄", "回心转意"],
        ["对象冷淡了怎么哄", "crush突然冷淡怎么回"],
        "情感场景。", "撩", "中",
        ["flirt", "absurd"]))
    # 13 分手
    C.append(_case(
        "fenshou-说分手", "对象/男朋友突然说分手",
        "应对提分手的试探，扳回一局又不卑微", "domain_specific", ["情感"], [],
        ["分手", "男朋友", "恋人"],
        ["对象突然说分手怎么回", "男朋友提分手怎么接"],
        "情感场景；慎用，需看是否真想分手。", "稳", "中",
        ["reverse-premise", "play-dumb"]))
    # 14 不合群
    C.append(_case(
        "buhequn-不合群", "别人说你不会做人、不合群、合不来",
        "回应别人对你'不合群/不会做人'的指责", "broad", [], [],
        ["不合群", "合群", "不合得来", "社交"],
        ["同事说我不合群怎么回", "室友说我不会做人怎么怼", "别人说我不合群怎么办"],
        "通用：谁说你不合群都能用，语气可冷可犀利。", "冷", "中",
        ["aloof", "reverse-premise"]))
    # 15 你急了
    C.append(_case(
        "nijile-你急了", "别人说你急了、破防了",
        "回应别人说你急了的扣帽子", "broad", [], [],
        ["你急了", "急", "破防"],
        ["同事说我急了怎么回", "别人说我破防了怎么怼", "朋友说我急了怎么办"],
        "通用扣帽子应对。", "犀利", "中",
        ["reverse-premise", "absurd"]))
    # 16 使唤你干活（broad：被使唤/打杂跨关系通用，不锁死同事/室友）
    C.append(_case(
        "tuodi-使唤你干活", "别人使唤你干活、拖地、跑腿，想守住边界",
        "守住边界，拒绝被使唤", "broad", [], [],
        ["使唤", "拖地", "干活", "跑腿", "打杂", "边界"],
        ["同事老使唤我干活怎么回", "室友使唤我做事怎么怼", "家里总让我跑腿怎么办", "别人让我打杂怎么拒绝"],
        "核心是守边界：被使唤/被要求打杂，跨关系通用（同事/室友/家人/朋友）。少数回复带夫妻梗，按关系选用。", "犀利", "中",
        ["reverse-premise", "absurd"]))
    # 17 比你贵
    C.append(_case(
        "bingui-比你贵", "别人攀比，暗示自己比你贵/更有档次",
        "阴阳对方攀比", "broad", [], [],
        ["贵", "比你贵", "攀比", "档次"],
        ["同事说他比我贵怎么回", "别人炫耀比我档次高怎么阴阳", "朋友说我用的没他好怎么回", "室友攀比我怎么怼"],
        "通用攀比应对，可阴阳。", "阴阳", "中",
        ["ironic", "absurd"]))
    # 18 学历（泛化：拿学历/读书说事，跨关系通用）
    C.append(_case(
        "daxuesheng-亏你还是大学生", "别人拿你的学历/读书/出身说事（如'亏你还是大学生'）",
        "回应别人对你学历/读书的阴阳", "broad", [], [],
        ["大学生", "学历", "读书", "阴阳", "书白读了"],
        ["同事说亏你还是大学生怎么回", "亲戚说我读书没用怎么怼", "朋友笑我学历怎么接", "别人拿我学历说事怎么办"],
        "核心是回应'拿学历/读书贬低你'，跨关系通用（同事/亲戚/长辈/朋友）。", "犀利", "中",
        ["reverse-premise", "ironic"]))
    # 19 亲戚催婚（修复 trigger↔response 失配）
    C.append(_with_rel(_case(
        "cuihun-亲戚催婚", "亲戚过年以关心为名追问你对象/催婚/揶揄，想敷衍怼回去",
        "应付亲戚过年的盘问/揶揄，不接茬", "relational", [], [],
        ["亲戚", "过年", "催婚", "对象", "盘问"],
        ["过年亲戚一直问我对象怎么回", "亲戚催婚怎么敷衍", "被亲戚盘问怎么怼", "亲戚问我怎么还不结婚怎么办"],
        "装傻+荒诞敷衍，专门对付'关心式盘问/催婚'；别用于有实质意义的提问。", "轻松", "中",
        ["play-dumb", "absurd", "reverse-premise"]), ["亲戚"]))
    # 20 升职拍马屁
    C.append(_case(
        "paimapi-升职靠拍马屁", "同事说你升职是靠拍马屁/讨好领导",
        "回应别人对你升职靠关系的质疑", "domain_specific", ["职场"], [],
        ["拍马屁", "升职", "讨好", "靠关系"],
        ["同事说我升职靠拍马屁怎么回", "别人说我讨好领导怎么办"],
        "职场限定。", "中", "中",
        ["play-dumb", "reverse-premise", "ironic"]))
    # 21 喂狗
    C.append(_case(
        "huotuichang-喂狗", "别人阴阳你（拿你的东西/行为说事，如'都拿来喂狗'）",
        "回应别人的阴阳/影射", "broad", [], [],
        ["喂狗", "阴阳", "影射"],
        ["同事阴阳我喂狗怎么回", "别人影射我怎么办", "室友阴阳我怎么办", "家里阴阳我吃东西怎么接"],
        "通用阴阳应对。", "犀利", "高",
        ["reverse-premise", "absurd"]))
    return C


def _attach_responses(C):
    # 逐案填 response（保留原始表达，不规范化）
    R = {
        "reject-tuoyanni": ["情绪问题不用和我反馈", "喜欢我那还得了", "忘了备注，请问你是",
                           "这么小的我抽象", "这边补偿您零元红包", "你对我产生了太多情感",
                           "其实我也没少骂你", "之前是喜欢我吗", "这句话没你长相有攻击性",
                           "那怎么办啊，宝宝", "恨比爱长久", "被你认可毫无意义", "宝宝都受着呗",
                           "个人情绪不用向我汇报", "对我有感觉就好", "你叫什么名字来着",
                           "原来他们说的都是真的", "你看到我哪一面，你就配哪一面",
                           "不知道，我的身材很曼妙"],
        "taimingan-太敏感": ["所以你要注意你的言行", "不敏感还不知道你这么坏", "你想太多了",
                           "你看你又过敏了", "你说哪里", "是你的恶意太明显", "这个人又装松弛",
                           "伤了你的小心脏", "确实对脏东西过敏",
                           "还是你更敏感，察觉到了我的敏感", "心虚了"],
        "guanxi-靠关系": ["我还能靠关系让你出去", "你不是吗，那你完了", "不知道啊，我发誓我考进来的",
                        "你领导是谁", "别老研究我怎么进来，抓紧研究自己怎么留下来吧",
                        "我就说我是考进来的", "你有高反吗", "进场还要靠关系",
                        "来之前我听过你的事", "你和我都在生产关系里"],
        "kadian-卡点下班": ["你不愿走，你死这呗", "我还卡点上班呢", "提前走不合适吧",
                          "不仅卡点我还卡盐，你已被淘汰", "爸呀，大哥家里没人等你了吗",
                          "少摸鱼你也能正常下班", "能者多劳，向您致敬", "这么关心我啊",
                          "精准踩点我的精彩人生", "工作时间 pdd，难怪你加班",
                          "我的爹啊大嗲，没人拦着你走"],
        "buxihuanni-领导不喜欢": ["因为我太像年轻时候的你吗", "但你的格局允许我在这",
                                "那你喜欢爸爸还是妈妈呀", "我能入职是因为你做不了主吗",
                                "太好了，还以为你对我没感觉"],
        "rangzuo-让座": ["可是您看上去最多十八岁", "我妈还说老而不死实为贼",
                        "你妈肯定没有机会教你点啥了", "哈哈我都七十了，和你差不多，只是从不干活身体好",
                        "我妈一直给我让座"],
        "shengao-喜欢高的": ["你昂首挺胸走进床底大哭一场好了", "别人是高冷帅哥，你是速冻矮子",
                            "遇到大型犬小心被配了", "人贩子拐了你二里地才发现不是小孩",
                            "丈母娘抱着你稀罕半天才发现不是孙子"],
        "yidubuhui-已读不回": ["你不要我了，我自己想的", "你在跟那个女的聊我就去死，没事我就试探一下",
                              "我的男孩不做作不矫情不心机不势利不爱我", "向你发起了恋爱申请，已取消"],
        "zhaohua-找话": ["我们的关系降温了，你说话能烧一点吗，这样就热了", "还在吗，哥哥送你条围巾",
                        "你已读不回漏财", "我们在一起吧", "听朋友说你没对象"],
        "haolei-说好累": ["晚上那种兼职要不别干了吧", "撒娇是男朋友的特权", "他从来不说累的",
                         "走上坡路是会累一点啦", "在我心里跑了一天能不累吗", "累的时候想想你父母"],
        "chicu-吃醋": ["他怕年后有偷猪的", "你不看他，怎么知道他看你", "你穿假鞋了",
                      "不知道，反正你的身材不曼妙", "他是我找来弄你的", "这个人在炫耀他市场好"],
        "hong-哄冷淡对象": ["我现在有驾照了，你想站马路中间等我吗", "现在几点了，我们好有缘，连手机时间都一样",
                           "你用我护手霜了，你终于承认你没用了", "我病了，急需一位自拍入药"],
        "fenshou-说分手": ["你也要和我分手", "我们不是一直都是朋友吗", "求求你不要分", "好的小谢"],
        "buhequn-不合群": ["跟你们合得来不如死了算了", "我是来上班不是来交朋友的",
                          "你上次不在他们也这样说你哎", "好感度不够，无法解锁对话",
                          "只是我的群里没有你", "少说废话"],
        "nijile-你急了": ["那你一定没急吧", "被狗咬了我已经急哭", "不是我急，是你没词了",
                        "你太敏感了", "对，看着马桶我尿急", "这么在意我的情绪"],
        "tuodi-使唤你干活": ["你出去就干净了", "拖把不是声控的", "宝宝多干活吧，瘦下来才知道自己底子有多差",
                            "我没敢怀孕，就叫你老公来拖", "我只会种地，你这个咋种",
                            "过年回家看看自己屁股上有没有脏"],
        "bingui-比你贵": ["便宜的栓不住你", "人普通，配饰就不普通", "果然比不上你一件货",
                         "后门没那么圆，深了吧", "人靠衣装，马靠铃铛", "你带着，我以为很便宜呢"],
        "daxuesheng-亏你还是大学生": ["你这样说是什么目的呢", "死鬼，大学哪有你好上啊",
                                     "大学不教这个，你要是上过就知道", "你父母也没教你礼貌",
                                     "笨才需要上学，不如您打小就通透"],
        "cuihun-亲戚催婚": ["有一些", "他们有家", "你安的什么心", "宫寒怕冻死孩子",
                           "拐弯抹角说我招笑呗", "像画儿，那我不早就挂墙上了"],
        "paimapi-升职靠拍马屁": ["啊是吗我怎么不知道", "那你教教我呗", "你不拍是因为不会吧"],
        "huotuichang-喂狗": ["你不要以为这样说我就会喂你", "怪不得我一吃就听见狗叫",
                            "大家说你那些居然是真的", "我一般都让狗看着我吃", "那我必须得给你来一根了"],
    }
    for c in C:
        c.response = R[c.case_id]
    return C


def main() -> None:
    kp = MarkdownKnowledgeProvider(ROOT)
    cases_dir = kp.cases_dir
    index_dir = kp.index_dir

    # 1) 旧 case 整体移入 raw/cases_v1/ 存档（非破坏；只移 v1 风格，即无 case_id 字段的旧文件）
    raw_cases_v1 = os.path.join(ROOT, "raw", "cases_v1")
    os.makedirs(raw_cases_v1, exist_ok=True)
    archived = 0
    for f in sorted(os.listdir(cases_dir)):
        if f.endswith(".md"):
            src = os.path.join(cases_dir, f)
            with open(src, encoding="utf-8") as _fh:
                head = _fh.read(200)
            if "case_id:" in head:
                continue    # 已是 v2 案例，不动
            dst = os.path.join(raw_cases_v1, f)
            if not os.path.exists(dst):
                shutil.move(src, dst)
                archived += 1
    print(f"旧案例已存档到 raw/cases_v1/（{archived} 个）")

    # 1b) 博主画像也属来源信息 → 移入 raw/creators/
    creators_src = os.path.join(ROOT, "knowledge", "creators")
    raw_creators = os.path.join(ROOT, "raw", "creators")
    if os.path.isdir(creators_src):
        os.makedirs(raw_creators, exist_ok=True)
        for f in sorted(os.listdir(creators_src)):
            s2 = os.path.join(creators_src, f)
            d2 = os.path.join(raw_creators, f)
            if os.path.isfile(s2) and not os.path.exists(d2):
                shutil.move(s2, d2)
        if not os.listdir(creators_src):
            os.rmdir(creators_src)
        print("博主画像已移入 raw/creators/")

    # 2) 写新 case（合并变体 + 语义纠正）
    C = _attach_responses(build_cases())
    for c in C:
        kp.upsert_case(c)

    # 3) 把新 case 挂到策略 examples
    for c in C:
        for sid in c.strategy_refs:
            s = kp.get_strategy(sid)
            if s and c.case_id not in s.examples:
                s.examples.append(c.case_id)
                kp.upsert_strategy(s)

    # 4) 来源归档（raw/case_sources.json：新 case → 旧来源）
    reg = {}
    for new_id, old_ids in SRC.items():
        provs = [{"old_id": oid, "creator": OLD_SRC[oid].get("creator", ""), "url": OLD_SRC[oid].get("url", ""),
                  "likes": OLD_SRC[oid].get("likes", "")} for oid in old_ids]
        reg[new_id] = {"merged_from": old_ids, "provenance": provs}
    os.makedirs(os.path.join(ROOT, "raw"), exist_ok=True)
    with open(os.path.join(ROOT, "raw", "case_sources.json"), "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)

    # 5) 复制原始投喂源到 raw/（完整溯源；开发环境保留，发布包不携带 raw/）
    raw_src = "/home/user/.doubao/agent_mode/workspace/.sessions/38444607544260098/attachments/原始投喂.md"
    if os.path.exists(raw_src):
        dst = os.path.join(ROOT, "raw", "原始投喂.md")
        if not os.path.exists(dst):
            shutil.copy(raw_src, dst)
        print("原始投喂源已归档到 raw/原始投喂.md")

    # 6) 自动重建索引
    r = build_index(ROOT)
    print(f"已写入 {len(C)} 个重构后案例；索引：{r['manifest']} 条案例 / {r['keywords']} 关键词")


if __name__ == "__main__":
    main()
