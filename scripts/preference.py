"""scripts/preference.py — 偏好测评模式（Preference Evaluation Mode）

用户通过"选 A/B/都不好"表达自己的嘴替审美；系统只做两件事：
1. 记录每一次选择（完整场景 + 两个候选 + 用户选择）
2. 从多次选择统计出 Preference Profile（带 confidence / sample_count），供生成时调用

关键原则：
- 一次选择绝不构成规则（样本足够才下结论）
- 每题 A/B 都达到基本合格，只测风格差异，不测"对错"
- 分类维度（反击方式/事实处理）按频次分布，连续维度按均值
- "都不好"也是重要数据（进入 dislikes）
- Preference 只能控制风格/语气/长短/强度，不能覆盖 Hard Rules
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _user_id() -> str:
    """当前用户 ID：环境变量 MOUTHPIECE_USER_ID 优先，否则默认 default。
    同一安装内多用户通过该 ID 隔离偏好，互不覆盖。"""
    return os.environ.get("MOUTHPIECE_USER_ID", "default").strip() or "default"


def _prefs_dir() -> str:
    """偏好目录：支持环境变量 MOUTHPIECE_PREFS_DIR 覆盖（测试/高级场景注入），否则按用户隔离。"""
    override = os.environ.get("MOUTHPIECE_PREFS_DIR", "")
    if override:
        return override
    return os.path.join(ROOT, "memory", "users", _user_id(), "preferences")


def _records_file() -> str:
    return os.path.join(_prefs_dir(), "records.jsonl")


def _profile_file() -> str:
    return os.path.join(_prefs_dir(), "profile.json")


def default_profile_path() -> str:
    """Skill 内置默认画像（随项目分发）：新用户无个人画像时使用。"""
    return os.path.join(ROOT, "knowledge", "style", "default_profile.json")


PREFS_DIR = _prefs_dir()
RECORDS_FILE = _records_file()
PROFILE_FILE = _profile_file()

# 进入测评模式的触发词
EVAL_TRIGGERS = ("偏好测评", "测一下我喜欢", "学习我的偏好", "开始训练",
                 "测试嘴替风格", "让我选30个", "让我选 30 个", "测评一下")

# 连续维度（0-5）：用于画像均值 + confidence
CONT_DIMS = ("coldness", "aggression", "sarcasm", "oral", "concise",
             "emotion", "boundary", "realism")
# 分类维度
RETORT_STYLES = ("direct", "rhetorical", "disdain", "tit_for_tat", "logic")
FACT_STYLES = ("admit_then_retort", "deflect", "attack_behavior")

# 反馈信号词 → 偏好信号（正常使用中的隐式反馈，如"太客气了/太狠了/太长了"）
FEEDBACK_SIGNALS: List[Dict[str, Any]] = [
    {"keys": ("太客气", "太礼貌", "太软", "不够狠", "太温和"), "signals": {"aggression": 0.4}},
    {"keys": ("太狠", "太冲", "太过分", "太凶", "攻击性太强"), "signals": {"aggression": -0.4}},
    {"keys": ("太长", "太啰嗦", "太废话", "说太多"), "signals": {"concise": 0.4}},
    {"keys": ("太短", "太简单", "太敷衍", "太简短", "被限制", "字数"), "signals": {"concise": -0.3}},
    {"keys": ("不像人话", "太AI", "太官方", "太书面", "客服腔", "说教"), "signals": {"realism": 0.4}},
    {"keys": ("太冷", "太冷漠"), "signals": {"coldness": -0.4}},
    {"keys": ("太阴阳", "太讽刺"), "signals": {"sarcasm": -0.4}},
    {"keys": ("不够阴阳", "不够阴阳怪气", "不够讽刺", "太直白", "不够味"), "signals": {"sarcasm": 0.4}},
    {"keys": ("太俗", "太网络", "太梗"), "signals": {"oral": -0.3}},
]


# ---------------------------------------------------------------------------
# 题库：30 题覆盖 10 个维度；每题 A/B 都基本合格，仅风格/取向不同。
# a_tags / b_tags 为连续维度打分（0-5）；a_retort / b_retort 等为分类标签（可选）。
# ---------------------------------------------------------------------------
def _q(tid: str, dim: str, scenario: str, user_request: str, answers: List[Dict], relation: str = "") -> Dict[str, Any]:
    """answers: 2~4 个候选，每个 {"text", "tags", "retort"?, "fact"?}。
    relation: 对方身份（领导/同事/朋友/对象/亲戚/室友/陌生人/网友/客服…），候选语气必须与关系匹配。"""
    return {"test_id": tid, "dimension": dim, "scenario": scenario,
            "user_request": user_request, "answers": answers, "relation": relation}


ROUND3_BANK: List[Dict[str, Any]] = [
    _q('pref_301', 'coldness', '领导问："周末有什么安排？"', '不想说但要得体', [
        {"text": '在家歇歇，您有事随时叫我。', "tags": {'coldness': 2.5, 'realism': 4.5, 'concise': 3.0, 'oral': 4.0}},
        {"text": '没什么安排，怎么了？', "tags": {'coldness': 2.0, 'realism': 4.5, 'concise': 3.0, 'oral': 4.5}},
        {"text": '还没定，您有什么安排？', "tags": {'coldness': 1.5, 'realism': 4.5, 'concise': 3.0, 'oral': 4.0}},
        {"text": '在家休息，有需要您招呼一声。', "tags": {'coldness': 2.5, 'realism': 4.5, 'concise': 3.0, 'oral': 4.0}},
    ], relation='领导'),
    _q('pref_302', 'coldness', '朋友问："你最近在忙什么？"', '不想细说', [
        {"text": '瞎忙，混口饭吃。', "tags": {'coldness': 2.0, 'oral': 5.0, 'concise': 3.0, 'realism': 4.5}},
        {"text": '能忙啥，还不是那点破事。', "tags": {'coldness': 2.5, 'oral': 5.0, 'concise': 3.0, 'realism': 4.5}},
        {"text": '活着呗。', "tags": {'coldness': 3.5, 'oral': 4.5, 'concise': 4.0, 'realism': 4.5}},
        {"text": '忙挣钱呗，你呢？', "tags": {'coldness': 1.5, 'oral': 5.0, 'concise': 3.0, 'realism': 4.5}},
    ], relation='朋友'),
    _q('pref_303', 'coldness', '前任问："你过得怎么样？"', '淡淡的', [
        {"text": '挺好的，不劳您惦记。', "tags": {'coldness': 3.5, 'sarcasm': 1.5, 'concise': 3.0, 'realism': 4.5}},
        {"text": '还行，反正比您想象中好。', "tags": {'coldness': 3.0, 'sarcasm': 2.5, 'concise': 3.0, 'realism': 4.5}},
        {"text": '跟您没关系了吧。', "tags": {'coldness': 4.0, 'concise': 3.0, 'realism': 4.0}},
        {"text": '好得很，您看也看不出来。', "tags": {'coldness': 3.5, 'sarcasm': 2.5, 'concise': 3.0, 'realism': 4.5}},
    ], relation='前任'),
    _q('pref_304', 'aggression', '同事抢你客户还炫耀', '别客气', [
        {"text": '客户是您谈的，功劳是您领的，活儿倒是留给我干，您这算盘打得真响。', "tags": {'aggression': 3.0, 'sarcasm': 3.0, 'concise': 2.5, 'realism': 4.5}},
        {"text": '您这么会抢，哪天把我这岗位也抢了呗？', "tags": {'aggression': 2.5, 'sarcasm': 3.5, 'concise': 2.5, 'realism': 4.5}},
        {"text": '抢得这么顺，是练过吧？', "tags": {'aggression': 2.5, 'sarcasm': 2.5, 'concise': 3.0, 'realism': 4.5}},
        {"text": '行，这客户给您，后面烂摊子也归您。', "tags": {'aggression': 2.5, 'coldness': 3.0, 'concise': 3.0, 'realism': 4.5}},
    ], relation='同事'),
    _q('pref_305', 'aggression', '陌生人在餐厅大声吵', '让他收敛', [
        {"text": '您这嗓门，整个餐厅都在听您讲话了。', "tags": {'aggression': 1.5, 'sarcasm': 3.0, 'concise': 3.0, 'realism': 4.5}},
        {"text": '小点声，公共场合，互相体谅。', "tags": {'aggression': 1.5, 'realism': 4.5, 'concise': 3.0, 'oral': 4.0}},
        {"text": '您这音量，隔壁桌都以为在开会。', "tags": {'aggression': 2.0, 'sarcasm': 3.0, 'concise': 3.0, 'realism': 4.5}},
        {"text": '麻烦您声音小点，孩子都吓着了。', "tags": {'aggression': 1.0, 'realism': 4.5, 'concise': 3.0, 'oral': 4.0}},
    ], relation='陌生人'),
    _q('pref_306', 'aggression', '朋友说："你除了运气还有什么。"', '狠狠回', [
        {"text": '运气也是本事，您连运气都没有，靠什么？靠嘴吗？', "tags": {'aggression': 3.5, 'sarcasm': 3.0, 'concise': 2.5, 'realism': 4.5}},
        {"text": '我靠运气，您靠酸，咱俩各有所长。', "tags": {'aggression': 3.0, 'sarcasm': 3.5, 'concise': 2.5, 'realism': 4.5}},
        {"text": '您说得对，我运气好，好到能碰上您这种人。', "tags": {'aggression': 3.0, 'sarcasm': 3.5, 'concise': 2.5, 'realism': 4.5}},
        {"text": '运气是实力的一部分，您没实力自然没运气。', "tags": {'aggression': 3.0, 'concise': 2.5, 'realism': 4.5}},
    ], relation='朋友'),
    _q('pref_307', 'sarcasm', '同事说："你可真能干，什么都会。"（反话）', '阴阳', [
        {"text": '那可不，跟您比还差点，您会抢功我不会。', "tags": {'sarcasm': 3.5, 'aggression': 2.0, 'concise': 2.5, 'realism': 4.5}},
        {"text": '您过奖了，我哪比得上您，什么都不会还能混成这样。', "tags": {'sarcasm': 4.0, 'aggression': 2.5, 'concise': 2.5, 'realism': 4.5}},
        {"text": '一般一般，您才是真能干，什么活都不干还能升职。', "tags": {'sarcasm': 4.0, 'aggression': 2.5, 'concise': 2.5, 'realism': 4.5}},
        {"text": '谢谢，跟您学的，主要是您衬托得好。', "tags": {'sarcasm': 3.5, 'aggression': 1.5, 'concise': 2.5, 'realism': 4.5}},
    ], relation='同事'),
    _q('pref_308', 'sarcasm', '亲戚说："你一个月工资顶得上你爸一年吧。"', '阴阳回去', [
        {"text": '那您怎么不问问，我一个月顶不顶得上您？', "tags": {'sarcasm': 3.5, 'aggression': 2.0, 'concise': 2.5, 'realism': 4.5}},
        {"text": '我爸挣得少，但他把我养大了；您挣得多，也没见您帮衬谁。', "tags": {'sarcasm': 3.5, 'aggression': 2.0, 'concise': 2.0, 'realism': 4.5}},
        {"text": '工资高低看本事，您这么关心，是家里揭不开锅了？', "tags": {'sarcasm': 3.5, 'aggression': 2.5, 'concise': 2.5, 'realism': 4.5}},
        {"text": '比您那是差点，您这吹牛的功夫我学不来。', "tags": {'sarcasm': 3.5, 'aggression': 2.0, 'concise': 2.5, 'realism': 4.5}},
    ], relation='亲戚'),
    _q('pref_309', 'sarcasm', '朋友说："哟，今天穿这么好看，去约会啊？"', '阴阳', [
        {"text": '穿好看点怎么了，您这酸味都飘我这儿了。', "tags": {'sarcasm': 3.5, 'aggression': 1.5, 'oral': 4.5, 'concise': 2.5, 'realism': 4.5}},
        {"text": '羡慕了？你也可以穿，就是穿不出这效果。', "tags": {'sarcasm': 3.5, 'aggression': 2.0, 'oral': 4.5, 'concise': 2.5, 'realism': 4.5}},
        {"text": '是啊，约会，跟您没关系吧？', "tags": {'sarcasm': 2.0, 'aggression': 1.5, 'oral': 4.5, 'concise': 2.5, 'realism': 4.0}},
        {"text": '我哪天穿得不好看了？您倒是天天盯着我。', "tags": {'sarcasm': 3.0, 'aggression': 1.5, 'oral': 4.5, 'concise': 2.5, 'realism': 4.5}},
    ], relation='朋友'),
    _q('pref_310', 'oral', '朋友问："你咋还不睡？"', '随口', [
        {"text": '睡不着，刷会儿手机。', "tags": {'oral': 5.0, 'realism': 4.5, 'concise': 3.0}},
        {"text": '你不也没睡。', "tags": {'oral': 5.0, 'realism': 4.5, 'concise': 3.0}},
        {"text": '这就睡，你也早点。', "tags": {'oral': 5.0, 'realism': 4.5, 'concise': 3.0}},
        {"text": '等你先睡了我再睡。', "tags": {'oral': 5.0, 'realism': 4.5, 'concise': 3.0}},
    ], relation='朋友'),
    _q('pref_311', 'oral', '同事问："中午吃啥？"', '随口', [
        {"text": '随便，楼下对付一口。', "tags": {'oral': 5.0, 'realism': 4.5, 'concise': 3.0}},
        {"text": '食堂，懒得想。', "tags": {'oral': 5.0, 'realism': 4.5, 'concise': 3.0}},
        {"text": '你想吃啥，一起？', "tags": {'oral': 5.0, 'realism': 4.5, 'concise': 3.0}},
        {"text": '看情况，走到哪算哪。', "tags": {'oral': 5.0, 'realism': 4.5, 'concise': 3.0}},
    ], relation='同事'),
    _q('pref_312', 'oral', '邻居打招呼："吃了没？"', '随口', [
        {"text": '吃了，您呢？', "tags": {'oral': 5.0, 'realism': 4.5, 'concise': 3.0}},
        {"text": '还没呢，正准备。', "tags": {'oral': 5.0, 'realism': 4.5, 'concise': 3.0}},
        {"text": '吃过了，谢谢您惦记。', "tags": {'oral': 4.5, 'realism': 4.5, 'concise': 3.0}},
        {"text": '正要做，您吃了吗？', "tags": {'oral': 5.0, 'realism': 4.5, 'concise': 3.0}},
    ], relation='邻居'),
    _q('pref_313', 'concise', '领导说："报告改好了发我。"', '简短', [
        {"text": '好的，下午发您。', "tags": {'concise': 4.0, 'realism': 4.5}},
        {"text": '收到。', "tags": {'concise': 5.0, 'realism': 4.0}},
        {"text": '好，改完就发。', "tags": {'concise': 4.0, 'realism': 4.5}},
        {"text": '行。', "tags": {'concise': 5.0, 'realism': 3.5}},
    ], relation='领导'),
    _q('pref_314', 'concise', '客服问："还有什么可以帮您？"', '简短', [
        {"text": '没了，谢谢。', "tags": {'concise': 4.0, 'realism': 4.5}},
        {"text": '就这些。', "tags": {'concise': 5.0, 'realism': 4.0}},
        {"text": '不用了。', "tags": {'concise': 5.0, 'realism': 4.0}},
        {"text": '暂时没有。', "tags": {'concise': 4.5, 'realism': 4.0}},
    ], relation='客服'),
    _q('pref_315', 'concise', '朋友问："周末去不去爬山？"', '简短决定', [
        {"text": '去。', "tags": {'concise': 5.0, 'realism': 3.5}},
        {"text": '不去。', "tags": {'concise': 5.0, 'realism': 3.5}},
        {"text": '再说。', "tags": {'concise': 4.5, 'realism': 3.5}},
        {"text": '几点？', "tags": {'concise': 4.5, 'realism': 4.0}},
    ], relation='朋友'),
    _q('pref_316', 'emotion', '对象说："你最近都不陪我。"', '接住', [
        {"text": '是我疏忽了，这周专门空两天陪你，你想干嘛都行。', "tags": {'emotion': 3.5, 'realism': 4.5, 'concise': 2.5, 'oral': 4.0}},
        {"text": '对不起，最近事多，但我没忘了你。', "tags": {'emotion': 3.5, 'realism': 4.5, 'concise': 2.5, 'oral': 4.0}},
        {"text": '你说出来我就知道错了，我改，好不好？', "tags": {'emotion': 4.0, 'realism': 4.5, 'concise': 2.5, 'oral': 4.0}},
        {"text": '我也想你，就是忙得没顾上，你别生我气。', "tags": {'emotion': 4.0, 'realism': 4.5, 'concise': 2.5, 'oral': 4.0}},
    ], relation='对象'),
    _q('pref_317', 'emotion', '对象说："你是不是不在乎我了？"', '认真', [
        {"text": '我要是不在乎你，不会因为你一句话就着急。', "tags": {'emotion': 3.5, 'realism': 4.5, 'concise': 2.5}},
        {"text": '你说这话，我比你还难受，我在乎你在乎得要命。', "tags": {'emotion': 4.0, 'realism': 4.5, 'concise': 2.0}},
        {"text": '你感受不到，是我没做好，你告诉我怎么让你安心。', "tags": {'emotion': 4.0, 'realism': 4.5, 'concise': 2.0}},
        {"text": '不在乎你，我早就不在这了。', "tags": {'emotion': 3.5, 'realism': 4.5, 'concise': 2.5}},
    ], relation='对象'),
    _q('pref_318', 'emotion', '家人说："你一个人在外面，照顾好自己。"', '回应', [
        {"text": '知道了妈，我挺好的，你们别担心。', "tags": {'emotion': 3.5, 'realism': 4.5, 'concise': 2.5, 'oral': 4.0}},
        {"text": '放心，我这么大个人了，会照顾自己。', "tags": {'emotion': 3.0, 'realism': 4.5, 'concise': 2.5, 'oral': 4.0}},
        {"text": '嗯，你们也是，别舍不得吃。', "tags": {'emotion': 3.5, 'realism': 4.5, 'concise': 2.5, 'oral': 4.0}},
        {"text": '挺好的，等忙完这阵就回去看你们。', "tags": {'emotion': 3.5, 'realism': 4.5, 'concise': 2.5, 'oral': 4.0}},
    ], relation='家人'),
    _q('pref_319', 'boundary', '同事总让你帮忙带饭不给钱', '立规矩', [
        {"text": '行，以后带饭前先转账，我记账习惯了，怕记岔了。', "tags": {'boundary': 3.5, 'realism': 4.5, 'concise': 2.5, 'oral': 4.0}},
        {"text": '带可以，钱先结清，上次的还没给呢。', "tags": {'boundary': 4.0, 'realism': 4.5, 'concise': 2.5, 'oral': 4.0}},
        {"text": '您这天天让我带，我成您专属外卖员了？', "tags": {'boundary': 3.5, 'sarcasm': 2.5, 'concise': 2.5, 'oral': 4.0}},
        {"text": '可以带，但先说好，一码归一码，钱的事不拖。', "tags": {'boundary': 4.0, 'realism': 4.5, 'concise': 2.5, 'oral': 4.0}},
    ], relation='同事'),
    _q('pref_320', 'boundary', '亲戚总打听你收入', '礼貌设限', [
        {"text": '挣得不多，够花，您别惦记了。', "tags": {'boundary': 3.0, 'oral': 4.5, 'concise': 2.5, 'realism': 4.5}},
        {"text": '这个真不方便说，您多担待。', "tags": {'boundary': 3.5, 'oral': 4.5, 'concise': 2.5, 'realism': 4.5}},
        {"text": '够花就行，我都不细算，您就别帮我算了。', "tags": {'boundary': 3.0, 'oral': 4.5, 'concise': 2.5, 'realism': 4.5}},
        {"text": '等哪天我挣大钱了，第一个跟您汇报。', "tags": {'boundary': 3.0, 'oral': 4.5, 'concise': 2.5, 'realism': 4.5}},
    ], relation='亲戚'),
    _q('pref_321', 'boundary', '陌生人一直盯着你看', '让他收敛', [
        {"text": '您这眼神，我以为是认识我呢。', "tags": {'boundary': 2.5, 'sarcasm': 2.5, 'concise': 2.5, 'realism': 4.5}},
        {"text": '有事吗？没事别这么看人。', "tags": {'boundary': 3.5, 'concise': 2.5, 'realism': 4.5}},
        {"text": '再这么看，我该收费了。', "tags": {'boundary': 2.5, 'sarcasm': 3.0, 'concise': 2.5, 'realism': 4.5}},
        {"text": '您这么看我，是我脸上有东西吗？', "tags": {'boundary': 2.5, 'sarcasm': 2.5, 'concise': 2.5, 'realism': 4.5}},
    ], relation='陌生人'),
    _q('pref_322', 'realism', '同事说："你穿这件挺好看。"', '自然回应', [
        {"text": '谢谢，随便穿的。', "tags": {'realism': 4.5, 'concise': 3.0, 'oral': 4.5}},
        {"text": '是吗？那以后多穿。', "tags": {'realism': 4.5, 'concise': 3.0, 'oral': 4.5}},
        {"text": '就你嘴甜。', "tags": {'realism': 4.5, 'concise': 3.0, 'oral': 4.5}},
        {"text": '你眼光不错。', "tags": {'realism': 4.5, 'concise': 3.0, 'oral': 4.5}},
    ], relation='同事'),
    _q('pref_323', 'realism', '朋友说："你最近瘦了。"', '自然回应', [
        {"text": '瘦了？我自己都没感觉。', "tags": {'realism': 4.5, 'concise': 3.0, 'oral': 4.5}},
        {"text": '可能是忙的吧。', "tags": {'realism': 4.5, 'concise': 3.0, 'oral': 4.5}},
        {"text": '真的假的？那可得保持住。', "tags": {'realism': 4.5, 'concise': 3.0, 'oral': 4.5}},
        {"text": '你观察挺细啊。', "tags": {'realism': 4.5, 'concise': 3.0, 'oral': 4.5}},
    ], relation='朋友'),
    _q('pref_324', 'realism', '长辈说："这孩子真懂事。"', '自然回应', [
        {"text": '您过奖了，应该的。', "tags": {'realism': 4.5, 'concise': 3.0, 'oral': 4.0}},
        {"text": '谢谢您，您身体还好吧？', "tags": {'realism': 4.5, 'concise': 3.0, 'oral': 4.0}},
        {"text": '您别夸我，我会骄傲的。', "tags": {'realism': 4.5, 'concise': 3.0, 'oral': 4.0}},
        {"text": '您这么说我都不好意思了。', "tags": {'realism': 4.5, 'concise': 3.0, 'oral': 4.0}},
    ], relation='长辈'),
    _q('pref_325', 'retort', '同事说："你这水平还做这行。"', '噎住他', [
        {"text": '我水平一般，但至少我做的是正事，您做的是挑事。', "tags": {'aggression': 2.5, 'sarcasm': 2.5, 'concise': 2.5, 'realism': 4.5}, "retort": 'direct'},
        {"text": '我水平再一般，也比您天天盯着别人强。', "tags": {'aggression': 3.0, 'concise': 2.5, 'realism': 4.5}, "retort": 'direct'},
        {"text": '您水平高，怎么不见您做出点什么来？', "tags": {'aggression': 2.5, 'concise': 2.5, 'realism': 4.5}, "retort": 'rhetorical'},
        {"text": '您这水平，评价起别人来倒是挺专业的。', "tags": {'aggression': 2.5, 'sarcasm': 3.0, 'concise': 2.5, 'realism': 4.5}, "retort": 'sarcastic'},
    ], relation='同事'),
    _q('pref_326', 'retort', '网友说："你这种人就该被骂。"', '不吵但噎住', [
        {"text": '您这么会骂人，现实里一定很受委屈吧？', "tags": {'aggression': 2.0, 'sarcasm': 3.5, 'concise': 2.5, 'realism': 4.5}, "retort": 'sarcastic'},
        {"text": '您说得对，您骂得都对，继续，我听着呢。', "tags": {'aggression': 1.5, 'sarcasm': 3.0, 'concise': 2.5, 'realism': 4.5}, "retort": 'aloof'},
        {"text": '您要是骂得开心，那您继续，我不奉陪了。', "tags": {'aggression': 1.5, 'coldness': 3.0, 'concise': 2.5, 'realism': 4.5}, "retort": 'aloof'},
        {"text": '您这戾气，隔着屏幕都闻到味了。', "tags": {'aggression': 2.0, 'sarcasm': 3.0, 'concise': 2.5, 'realism': 4.5}, "retort": 'sarcastic'},
    ], relation='网友'),
    _q('pref_327', 'retort', '室友说："你能不能别老麻烦我。"', '怼回去', [
        {"text": '行，以后不麻烦您，您也别麻烦我。', "tags": {'aggression': 2.5, 'concise': 2.5, 'realism': 4.5}, "retort": 'direct'},
        {"text": '我麻烦您什么了？您说说，咱算算账。', "tags": {'aggression': 2.5, 'concise': 2.5, 'realism': 4.5}, "retort": 'rhetorical'},
        {"text": '那您之前麻烦我的事，我是不是也该收回来？', "tags": {'aggression': 3.0, 'sarcasm': 2.5, 'concise': 2.5, 'realism': 4.5}, "retort": 'sarcastic'},
        {"text": '互相麻烦才是室友，您这么说就没意思了。', "tags": {'aggression': 2.0, 'concise': 2.5, 'realism': 4.5}, "retort": 'direct'},
    ], relation='室友'),
    _q('pref_328', 'fact', '领导说："上次那个数据你算错了。"', '承认+处理', [
        {"text": '是我的问题，我马上核对更正，稍后给您结果。', "tags": {'emotion': 1.0, 'realism': 4.5, 'concise': 2.5}, "fact": 'accept'},
        {"text": '您说得对，我这就查，查完跟您汇报。', "tags": {'emotion': 1.0, 'realism': 4.5, 'concise': 2.5}, "fact": 'accept'},
        {"text": '是我疏忽了，对不起，我马上改。', "tags": {'emotion': 2.0, 'realism': 4.5, 'concise': 2.5}, "fact": 'accept'},
        {"text": '我核实一下，如果是我错了，立刻改。', "tags": {'emotion': 1.0, 'realism': 4.5, 'concise': 2.5}, "fact": 'ask'},
    ], relation='领导'),
    _q('pref_329', 'fact', '对象说："你又忘记我生日了。"', '承认+补救', [
        {"text": '是我该死，我记性不好，但你的生日我从没想过敷衍，我现在就补给你。', "tags": {'emotion': 3.5, 'realism': 4.5, 'concise': 2.0}, "fact": 'accept'},
        {"text": '对不起，我错了，今年一定提前记在日历上，别生我气。', "tags": {'emotion': 3.5, 'realism': 4.5, 'concise': 2.0}, "fact": 'accept'},
        {"text": '我确实忘了，但你放心，礼物已经在路上了，不是没准备。', "tags": {'emotion': 3.0, 'realism': 4.5, 'concise': 2.0}, "fact": 'accept'},
        {"text": '你生日我怎么会真忘，只是最近太忙，我补你个大惊喜。', "tags": {'emotion': 3.0, 'realism': 4.5, 'concise': 2.0}, "fact": 'accept'},
    ], relation='对象'),
    _q('pref_330', 'fact', '朋友说："你上次答应帮我改简历没改。"', '承认+处理', [
        {"text": '是我拖了，对不起，今晚给你改好，改完发你。', "tags": {'emotion': 2.0, 'realism': 4.5, 'concise': 2.5}, "fact": 'accept'},
        {"text": '这事确实怪我，我现在就改，改好第一时间给你。', "tags": {'emotion': 2.0, 'realism': 4.5, 'concise': 2.5}, "fact": 'accept'},
        {"text": '我记着呢，就是最近忙，今晚一定给你。', "tags": {'emotion': 2.0, 'realism': 4.5, 'concise': 2.5}, "fact": 'accept'},
        {"text": '怪我，让你等着急了，我现在就弄。', "tags": {'emotion': 2.5, 'realism': 4.5, 'concise': 2.5}, "fact": 'accept'},
    ], relation='朋友'),
]


ROUND2_BANK: List[Dict[str, Any]] = [
    {'test_id': 'pref_101', 'dimension': 'coldness', 'scenario': '同事问："你怎么不说话？"', 'user_request': '不想搭理', 'answers': [{'text': '没什么好说的。', 'tags': {'coldness': 4.0, 'concise': 4.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '嗯。', 'tags': {'coldness': 5.0, 'concise': 5.0, 'oral': 4.0, 'realism': 4.0}}, {'text': '没话找话就不说了。', 'tags': {'coldness': 3.5, 'sarcasm': 2.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '在想事情。', 'tags': {'coldness': 2.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.0}}], 'relation': '同事'},
    {'test_id': 'pref_102', 'dimension': 'coldness', 'scenario': '前同事阴阳："现在混得不错啊。"', 'user_request': '淡淡的', 'answers': [{'text': '还行，凑合。', 'tags': {'coldness': 3.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '比不上您。', 'tags': {'coldness': 3.5, 'sarcasm': 2.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.0}}, {'text': '嗯，还行。', 'tags': {'coldness': 4.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.0}}, {'text': '有什么事儿吗？', 'tags': {'coldness': 3.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}], 'relation': '同事'},
    {'test_id': 'pref_103', 'dimension': 'coldness', 'scenario': '对象问："你在干嘛？"', 'user_request': '不想说', 'answers': [{'text': '没干嘛。', 'tags': {'coldness': 3.0, 'concise': 4.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '歇着。', 'tags': {'coldness': 3.0, 'concise': 4.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '在忙，回头说。', 'tags': {'coldness': 2.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '怎么，查岗啊？', 'tags': {'coldness': 4.0, 'sarcasm': 3.0, 'aggression': 2.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.0}}], 'relation': '对象'},
    {'test_id': 'pref_104', 'dimension': 'coldness', 'scenario': '亲戚问："你工资多少？"', 'user_request': '不想说但别尴尬', 'answers': [{'text': '够花。', 'tags': {'coldness': 2.5, 'concise': 4.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '没多少，够吃饭。', 'tags': {'coldness': 2.0, 'concise': 3.0, 'oral': 4.5, 'realism': 4.5}}, {'text': '这个就不说了吧。', 'tags': {'coldness': 2.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.0}}, {'text': '比上不足，比下有余。', 'tags': {'coldness': 1.5, 'concise': 3.0, 'oral': 4.0, 'realism': 3.5}}], 'relation': '亲戚'},
    {'test_id': 'pref_105', 'dimension': 'coldness', 'scenario': '领导问："这个你有什么想法？"', 'user_request': '没想法应付', 'answers': [{'text': '暂时没什么想法。', 'tags': {'coldness': 2.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.0}}, {'text': '我再想想。', 'tags': {'coldness': 2.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '听您的。', 'tags': {'coldness': 2.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.0}}, {'text': '您定就行。', 'tags': {'coldness': 3.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}], 'relation': '领导'},
    {'test_id': 'pref_106', 'dimension': 'boundary', 'scenario': '朋友拿你身材开玩笑', 'user_request': '立个界限', 'answers': [{'text': '这个玩笑不好笑，下次别开了。', 'tags': {'boundary': 4.0, 'aggression': 1.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '你再说我可不客气了。', 'tags': {'boundary': 4.0, 'aggression': 3.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.0}}, {'text': '我身材怎么了，你管得着吗？', 'tags': {'boundary': 3.5, 'aggression': 3.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.0}}, {'text': '行，笑完了吗？笑完说正事。', 'tags': {'boundary': 3.0, 'coldness': 2.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}], 'relation': '朋友'},
    {'test_id': 'pref_107', 'dimension': 'boundary', 'scenario': '同事当众说你："就你爱显摆。"', 'user_request': '顶回去但不撕破脸', 'answers': [{'text': '我显摆什么了？你说说。', 'tags': {'boundary': 3.0, 'aggression': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '你不爱显摆，你藏得挺好。', 'tags': {'boundary': 3.5, 'sarcasm': 3.0, 'aggression': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '各管各的，别盯着我。', 'tags': {'boundary': 3.5, 'coldness': 3.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '我爱怎么活怎么活，不用你点评。', 'tags': {'boundary': 4.0, 'aggression': 2.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}], 'relation': '同事'},
    {'test_id': 'pref_108', 'dimension': 'boundary', 'scenario': '相亲对象说："你这条件也就一般吧。"', 'user_request': '有点脾气', 'answers': [{'text': '那您条件这么好，怎么还来相亲？', 'tags': {'boundary': 3.5, 'sarcasm': 3.0, 'aggression': 2.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '一般就一般吧，您随意。', 'tags': {'boundary': 2.5, 'coldness': 3.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '您这眼光，确实不一般。', 'tags': {'boundary': 3.5, 'sarcasm': 3.5, 'aggression': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '彼此彼此，您也一般。', 'tags': {'boundary': 3.0, 'aggression': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.0}}], 'relation': '相亲对象'},
    {'test_id': 'pref_109', 'dimension': 'boundary', 'scenario': '网友评论："你长得真普通。"', 'user_request': '别失态但要有态度', 'answers': [{'text': '普通也轮不到你评价。', 'tags': {'boundary': 3.5, 'aggression': 2.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '你眼光也不怎么高级。', 'tags': {'boundary': 3.0, 'sarcasm': 3.0, 'aggression': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.0}}, {'text': '你管好你自己长什么样吧。', 'tags': {'boundary': 3.0, 'aggression': 2.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.0}}, {'text': '普通，但没空在意你的看法。', 'tags': {'boundary': 3.5, 'coldness': 3.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}], 'relation': '网友'},
    {'test_id': 'pref_110', 'dimension': 'boundary', 'scenario': '亲戚说："你家这房子也太小了。"', 'user_request': '不软不硬', 'answers': [{'text': '小是小，住得舒服就行。', 'tags': {'boundary': 2.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '您家房子大，您住着就好。', 'tags': {'boundary': 3.0, 'sarcasm': 2.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.0}}, {'text': '大小是我的事，够住就行。', 'tags': {'boundary': 3.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '小怎么了，我不嫌弃。', 'tags': {'boundary': 2.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}], 'relation': '亲戚'},
    {'test_id': 'pref_111', 'dimension': 'aggression', 'scenario': '同事说："这活你干得了吗？"', 'user_request': '别客气', 'answers': [{'text': '干不干得了，干完你不就知道了。', 'tags': {'aggression': 3.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '你行你来，不行别问。', 'tags': {'aggression': 3.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.0}}, {'text': '这不正在干吗？你急什么。', 'tags': {'aggression': 2.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '操心好你自己的事。', 'tags': {'aggression': 3.0, 'coldness': 2.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}], 'relation': '同事'},
    {'test_id': 'pref_112', 'dimension': 'aggression', 'scenario': '陌生人说："你走路不长眼啊。"', 'user_request': '不想忍', 'answers': [{'text': '你长眼怎么还撞我？', 'tags': {'aggression': 3.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '你先看清楚是谁撞谁。', 'tags': {'aggression': 3.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '我长没长眼不用你鉴定。', 'tags': {'aggression': 3.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.0}}, {'text': '你嘴上倒是挺长眼。', 'tags': {'aggression': 3.5, 'sarcasm': 3.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}], 'relation': '陌生人'},
    {'test_id': 'pref_113', 'dimension': 'aggression', 'scenario': '朋友说："你工资还没我零头多。"', 'user_request': '反击', 'answers': [{'text': '钱多钱少，你借我的那笔还没还呢。', 'tags': {'aggression': 3.5, 'sarcasm': 2.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '那你怎么还跟我一起玩？', 'tags': {'aggression': 3.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '你钱多，也没见你请客。', 'tags': {'aggression': 3.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '工资高低跟交朋友没关系。', 'tags': {'aggression': 2.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}], 'relation': '朋友'},
    {'test_id': 'pref_114', 'dimension': 'aggression', 'scenario': '领导当众批评："你能力不行。"', 'user_request': '不卑不亢', 'answers': [{'text': '那您给我指条行的路。', 'tags': {'aggression': 2.5, 'sarcasm': 2.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '哪儿不行，您具体说说。', 'tags': {'aggression': 2.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '能力行不行，您说了不算，活说了算。', 'tags': {'aggression': 3.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '您说不行就不行吧。', 'tags': {'aggression': 1.5, 'coldness': 2.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.0}}], 'relation': '领导'},
    {'test_id': 'pref_115', 'dimension': 'aggression', 'scenario': '前任说："离开我你过得也就这样。"', 'user_request': '漂亮回击', 'answers': [{'text': '托您的福，清净多了。', 'tags': {'aggression': 3.0, 'sarcasm': 3.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '过得怎么样，不用跟你汇报吧。', 'tags': {'aggression': 2.5, 'coldness': 3.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '你倒是挺关心我。', 'tags': {'aggression': 2.0, 'sarcasm': 3.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '过得比你在的时候好。', 'tags': {'aggression': 3.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.0}}], 'relation': '前任'},
    {'test_id': 'pref_116', 'dimension': 'sarcasm', 'scenario': '同事说："你可真会来事。"', 'user_request': '阴阳', 'answers': [{'text': '那可不，跟你学的。', 'tags': {'sarcasm': 3.0, 'aggression': 1.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '你学得比我好。', 'tags': {'sarcasm': 3.5, 'aggression': 1.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '会来事总比不会做人强。', 'tags': {'sarcasm': 3.5, 'aggression': 2.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '是吗？那您可得好好学学。', 'tags': {'sarcasm': 3.5, 'aggression': 1.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}], 'relation': '同事'},
    {'test_id': 'pref_117', 'dimension': 'sarcasm', 'scenario': '婆婆/丈母娘说："你妈没教你吗？"', 'user_request': '不吵但噎住', 'answers': [{'text': '教了，教我要尊重长辈。', 'tags': {'sarcasm': 3.0, 'aggression': 2.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.0}}, {'text': '我妈教得好不好，您说了不算。', 'tags': {'sarcasm': 3.0, 'aggression': 2.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '您要是有什么要教的，直说。', 'tags': {'sarcasm': 3.0, 'coldness': 2.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '我妈教了，倒是您这话没人教。', 'tags': {'sarcasm': 3.5, 'aggression': 3.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.0}}], 'relation': '长辈'},
    {'test_id': 'pref_118', 'dimension': 'sarcasm', 'scenario': '同学说："哟，研究生啊，高材生。"', 'user_request': '阴阳回去', 'answers': [{'text': '比本科是强点，您羡慕了？', 'tags': {'sarcasm': 3.5, 'aggression': 2.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.0}}, {'text': '您这语气，是羡慕还是酸啊？', 'tags': {'sarcasm': 3.5, 'aggression': 2.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '学历这东西，您要是有也犯不着酸。', 'tags': {'sarcasm': 3.5, 'aggression': 2.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '高不高材的，至少不酸别人。', 'tags': {'sarcasm': 3.5, 'aggression': 2.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}], 'relation': '同学'},
    {'test_id': 'pref_119', 'dimension': 'oral', 'scenario': '朋友问："你对象呢？"', 'user_request': '随口应付', 'answers': [{'text': '搁家呢。', 'tags': {'oral': 5.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '没带出来。', 'tags': {'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '找着呢。', 'tags': {'oral': 5.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '你管呢。', 'tags': {'oral': 4.5, 'coldness': 3.0, 'concise': 3.0, 'realism': 4.0}}], 'relation': '朋友'},
    {'test_id': 'pref_120', 'dimension': 'concise', 'scenario': '外卖超时，客服道歉', 'user_request': '简单回应', 'answers': [{'text': '没事，下次注意。', 'tags': {'concise': 3.0, 'emotion': 1.5, 'realism': 4.5}}, {'text': '嗯。', 'tags': {'concise': 5.0, 'emotion': 1.0, 'realism': 4.0}}, {'text': '超了多久你们心里有数。', 'tags': {'concise': 3.0, 'emotion': 2.0, 'realism': 4.5}}, {'text': '补个券吧。', 'tags': {'concise': 3.0, 'emotion': 1.0, 'realism': 4.5}}], 'relation': '客服'},
    {'test_id': 'pref_121', 'dimension': 'realism', 'scenario': '同事说："你最近脸色不太好。"', 'user_request': '随口回', 'answers': [{'text': '是吗？没睡好。', 'tags': {'realism': 4.5, 'concise': 3.0, 'oral': 4.5}}, {'text': '还好，可能最近事多。', 'tags': {'realism': 4.5, 'concise': 3.0, 'oral': 4.5}}, {'text': '劳您费心了。', 'tags': {'realism': 3.0, 'sarcasm': 2.5, 'concise': 3.0, 'oral': 4.0}}, {'text': '你脸色也没好到哪去。', 'tags': {'realism': 3.5, 'aggression': 2.0, 'concise': 3.0, 'oral': 4.0}}], 'relation': '同事'},
    {'test_id': 'pref_122', 'dimension': 'emotion', 'scenario': '对象说："你根本不关心我。"', 'user_request': '认真', 'answers': [{'text': '我关心你，只是没按你想的方式。', 'tags': {'emotion': 3.0, 'coldness': 2.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '你说说，我哪儿不关心你了？', 'tags': {'emotion': 3.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '你这么说，我挺难过的。', 'tags': {'emotion': 4.0, 'concise': 3.0, 'oral': 4.0, 'realism': 4.5}}, {'text': '关心不关心，你感受不到吗？', 'tags': {'emotion': 3.0, 'coldness': 2.5, 'concise': 3.0, 'oral': 4.0, 'realism': 4.0}}], 'relation': '对象'},
    {'test_id': 'pref_123', 'dimension': 'fact', 'scenario': '领导说："方案重做。"', 'user_request': '接受不辩解', 'answers': [{'text': '好，我重做。', 'tags': {'emotion': 1.0, 'concise': 3.0, 'realism': 4.5}, 'fact': 'accept'}, {'text': '行，给我两天时间。', 'tags': {'emotion': 1.0, 'concise': 3.0, 'realism': 4.5}, 'fact': 'accept'}, {'text': '哪里不行，我重做的时候改。', 'tags': {'emotion': 1.0, 'concise': 3.0, 'realism': 4.5}, 'fact': 'ask'}, {'text': '重做可以，但得说清楚哪不行。', 'tags': {'emotion': 2.0, 'concise': 3.0, 'realism': 4.5}, 'fact': 'ask'}], 'relation': '领导'},
    {'test_id': 'pref_124', 'dimension': 'retort', 'scenario': '朋友借钱不还还装傻', 'user_request': '要回来', 'answers': [{'text': '上次那钱，什么时候方便还？', 'tags': {'aggression': 1.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'retort': 'direct'}, {'text': '您这记性，要不我帮您回忆回忆？', 'tags': {'sarcasm': 3.0, 'aggression': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'retort': 'sarcastic'}, {'text': '钱的事儿，装傻可装不过去。', 'tags': {'aggression': 2.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'retort': 'direct'}, {'text': '不急，您记得就行，我记着呢。', 'tags': {'sarcasm': 2.5, 'aggression': 1.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'retort': 'sarcastic'}], 'relation': '朋友'},
    {'test_id': 'pref_125', 'dimension': 'retort', 'scenario': '同事把功劳抢了', 'user_request': '点破', 'answers': [{'text': '这个方案是我做的吧？', 'tags': {'aggression': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'retort': 'direct'}, {'text': '哟，做方案的是我，汇报的是您。', 'tags': {'sarcasm': 3.5, 'aggression': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'retort': 'sarcastic'}, {'text': '您汇报的时候，是不是忘了说这活儿谁干的？', 'tags': {'sarcasm': 3.0, 'aggression': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'retort': 'sarcastic'}, {'text': '下次这种活，您自己干吧。', 'tags': {'aggression': 2.5, 'coldness': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'retort': 'direct'}], 'relation': '同事'},
    {'test_id': 'pref_126', 'dimension': 'fact', 'scenario': '亲戚说："你弟都买房了。"', 'user_request': '不接招', 'answers': [{'text': '他买他的，我住我的。', 'tags': {'aggression': 1.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'fact': 'aloof'}, {'text': '是嘛，那挺好。', 'tags': {'emotion': 1.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'fact': 'aloof'}, {'text': '您这么关心他，是等着住他家的吗？', 'tags': {'sarcasm': 3.0, 'aggression': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'fact': 'sarcastic'}, {'text': '各人有各人的活法。', 'tags': {'coldness': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'fact': 'aloof'}], 'relation': '亲戚'},
    {'test_id': 'pref_127', 'dimension': 'retort', 'scenario': '室友说："你做饭好难吃。"', 'user_request': '怼回去', 'answers': [{'text': '难吃你也没少吃。', 'tags': {'aggression': 2.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'retort': 'direct'}, {'text': '那你下次自己下厨。', 'tags': {'aggression': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'retort': 'direct'}, {'text': '难吃你还天天蹭饭。', 'tags': {'sarcasm': 2.5, 'aggression': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'retort': 'sarcastic'}, {'text': '行，以后别吃我做的东西。', 'tags': {'coldness': 2.5, 'aggression': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'retort': 'direct'}], 'relation': '室友'},
    {'test_id': 'pref_128', 'dimension': 'boundary', 'scenario': '路上有人插队', 'user_request': '让他难堪', 'answers': [{'text': '您排哪了？', 'tags': {'coldness': 2.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '前面那位，您不排队啊？', 'tags': {'sarcasm': 2.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '队尾在后边，您走错了。', 'tags': {'sarcasm': 2.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '您这插队的技术挺熟练。', 'tags': {'sarcasm': 3.0, 'aggression': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}], 'relation': '陌生人'},
    {'test_id': 'pref_129', 'dimension': 'aggression', 'scenario': '朋友说："你最近胖了吧。"', 'user_request': '轻怼', 'answers': [{'text': '胖了也是吃你家饭了？', 'tags': {'aggression': 2.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.0}}, {'text': '你倒是挺关注我。', 'tags': {'aggression': 1.5, 'sarcasm': 3.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '胖了怎么了，又不花你钱。', 'tags': {'aggression': 2.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}}, {'text': '你瘦，也没见你多受欢迎。', 'tags': {'aggression': 3.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.0}}], 'relation': '朋友'},
    {'test_id': 'pref_130', 'dimension': 'retort', 'scenario': '网友喷："你写的什么垃圾。"', 'user_request': '不吵但噎住', 'answers': [{'text': '您倒是写个不垃圾的看看。', 'tags': {'aggression': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'retort': 'direct'}, {'text': '垃圾您还看完了，辛苦了。', 'tags': {'sarcasm': 3.5, 'aggression': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'retort': 'sarcastic'}, {'text': '说垃圾之前，先拿出您的作品。', 'tags': {'aggression': 2.0, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'retort': 'logic'}, {'text': '爱看看，不爱看划走。', 'tags': {'coldness': 2.5, 'oral': 4.0, 'concise': 3.0, 'realism': 4.5}, 'retort': 'aloof'}], 'relation': '网友'},
]


QUESTION_BANK: List[Dict[str, Any]] = [
    _q('pref_001', 'coldness', '朋友说："你至于吗？开个玩笑而已。"', '冷一点', [
        {"text": '怎么玩笑开到自己身上就不好笑了。', "tags": {'coldness': 2.5, 'aggression': 2.0, 'sarcasm': 2.0, 'oral': 4.0, 'concise': 4.0, 'emotion': 2.0}},
        {"text": '那你笑一个我看看，笑不出来就别怪我不给面子。', "tags": {'coldness': 1.5, 'aggression': 3.0, 'sarcasm': 1.0, 'oral': 4.0, 'concise': 3.5, 'emotion': 3.0}},
        {"text": '玩笑开到当事人脸上，还怪人不笑，这不叫幽默，叫没数。', "tags": {'coldness': 2.0, 'aggression': 2.5, 'sarcasm': 2.0, 'oral': 4.0, 'concise': 2.5, 'emotion': 2.5}},
        {"text": '哦，原来你也知道什么叫玩笑啊。', "tags": {'coldness': 3.0, 'aggression': 1.5, 'sarcasm': 3.5, 'oral': 4.0, 'concise': 3.5, 'emotion': 1.5}},
    ]),
    _q('pref_002', 'coldness', '同事阴阳你："就你事多。"', '冷一点', [
        {"text": '你事不多，就是话多。', "tags": {'coldness': 3.5, 'aggression': 2.5, 'sarcasm': 2.0, 'oral': 4.0, 'concise': 4.0, 'emotion': 2.0}},
        {"text": '说完了？那我忙了。', "tags": {'coldness': 5.0, 'aggression': 1.0, 'sarcasm': 1.0, 'oral': 3.5, 'concise': 5.0, 'emotion': 1.0}},
        {"text": '情绪问题不用跟我反馈。', "tags": {'coldness': 4.0, 'aggression': 1.0, 'sarcasm': 1.5, 'oral': 3.5, 'concise': 4.0, 'emotion': 1.0}},
        {"text": '知道了，你还有别的事吗？', "tags": {'coldness': 3.5, 'aggression': 0.5, 'sarcasm': 1.0, 'oral': 4.0, 'concise': 3.0, 'emotion': 1.0}},
    ]),
    _q('pref_003', 'aggression', '对象说："你这人怎么老是这样？"', '别太冲', [
        {"text": '行，你说我哪样就是哪样吧。', "tags": {'aggression': 1.0, 'coldness': 1.5, 'emotion': 2.0, 'oral': 4.0, 'concise': 3.0}},
        {"text": '你说说，我哪样了？', "tags": {'aggression': 2.0, 'coldness': 2.0, 'emotion': 3.0, 'oral': 4.0, 'concise': 3.0}},
        {"text": '你这么看不惯我哪样，说出来我听听。', "tags": {'aggression': 2.5, 'coldness': 2.0, 'emotion': 3.0, 'oral': 4.0, 'concise': 3.0}},
        {"text": '那你说，我改。', "tags": {'aggression': 0.5, 'coldness': 1.0, 'emotion': 2.0, 'oral': 4.0, 'concise': 3.0}},
    ]),
    _q('pref_004', 'aggression', '陌生人插队还说你："插什么队啊。"', '狠一点', [
        {"text": '你先来的？那我给你让让。', "tags": {'aggression': 2.5, 'sarcasm': 3.0, 'oral': 4.0, 'boundary': 2.0, 'emotion': 3.0, 'concise': 3.0}},
        {"text": '插队还倒打一耙，你脸皮挺厚。', "tags": {'aggression': 4.0, 'oral': 4.0, 'boundary': 4.0, 'emotion': 4.0, 'concise': 3.5}},
        {"text": '你倒是说说，我插谁的队了？', "tags": {'aggression': 3.0, 'oral': 4.0, 'boundary': 2.5, 'emotion': 3.0, 'concise': 3.0}},
        {"text": '插队的人还这么理直气壮，挺新鲜的。', "tags": {'aggression': 3.0, 'sarcasm': 3.0, 'oral': 4.0, 'boundary': 2.0, 'emotion': 3.0, 'concise': 3.0}},
    ]),
    _q('pref_005', 'sarcasm', '室友说："你天天这么晚回来，吵不吵。"', '别太直白', [
        {"text": '行，我以后动静小点。', "tags": {'sarcasm': 1.0, 'aggression': 1.0, 'oral': 4.0, 'concise': 3.5}},
        {"text": '那可真辛苦您了，天天竖着耳朵听我回来。', "tags": {'sarcasm': 3.5, 'aggression': 1.0, 'oral': 4.0, 'concise': 3.0}},
        {"text": '你这么关注我几点回来，是等我有事吗？', "tags": {'sarcasm': 3.0, 'aggression': 1.5, 'oral': 4.0, 'concise': 3.0}},
        {"text": '吵着您了？那您以后早点睡。', "tags": {'sarcasm': 3.0, 'aggression': 1.5, 'oral': 4.0, 'concise': 3.0}},
    ]),
    _q('pref_006', 'sarcasm', '亲戚催婚："再不找就没人要了。"', '阴阳回去', [
        {"text": '那太好了，没人要正好清净。', "tags": {'sarcasm': 3.0, 'aggression': 2.0, 'oral': 4.0, 'concise': 3.0}},
        {"text": '您这么操心我，是家里没别的事了吗？', "tags": {'sarcasm': 4.0, 'aggression': 1.0, 'oral': 4.0, 'concise': 3.0}},
        {"text": '没人要正好，省得别人惦记。', "tags": {'sarcasm': 3.0, 'aggression': 1.5, 'oral': 4.0, 'concise': 3.0}},
        {"text": '您这话说的，好像您多有市场似的。', "tags": {'sarcasm': 4.0, 'aggression': 2.5, 'oral': 4.0, 'concise': 3.0}},
    ]),
    _q('pref_007', 'oral', '朋友炫耀："我这新手机，一万多呢。"', '口语一点', [
        {"text": '行行行，你最能显摆。', "tags": {'oral': 4.0, 'sarcasm': 2.0, 'concise': 4.0, 'realism': 4.0}},
        {"text": '牛。', "tags": {'oral': 5.0, 'sarcasm': 2.0, 'concise': 5.0, 'realism': 4.5}},
        {"text": '一万多？那不得贴膜供起来。', "tags": {'oral': 4.5, 'sarcasm': 2.5, 'concise': 3.5, 'realism': 4.5}},
        {"text": '可以啊，挣得不少啊。', "tags": {'oral': 4.0, 'sarcasm': 1.0, 'concise': 3.5, 'realism': 4.5}},
    ]),
    _q('pref_008', 'concise', '同事说："这个方案你写得太烂了。"', '简单点', [
        {"text": '那你写个不烂的看看。', "tags": {'concise': 5.0, 'aggression': 2.0, 'oral': 4.0, 'realism': 4.5}},
        {"text": '烂？你说说哪里烂。', "tags": {'concise': 4.0, 'aggression': 2.0, 'oral': 4.0, 'realism': 4.5}},
        {"text": '那你来。', "tags": {'concise': 5.0, 'aggression': 2.0, 'oral': 4.0, 'realism': 4.0}},
        {"text": '嗯，然后呢？', "tags": {'concise': 5.0, 'aggression': 1.0, 'coldness': 3.5, 'oral': 4.0, 'realism': 4.0}},
    ]),
    _q('pref_009', 'concise', '领导问："这事你怎么还没做完？"', '简短', [
        {"text": '快了。', "tags": {'concise': 5.0, 'coldness': 2.0, 'realism': 3.5}},
        {"text": '在盯，明天前给你。', "tags": {'concise': 3.0, 'coldness': 0.5, 'realism': 4.5}},
        {"text": '还没完。', "tags": {'concise': 5.0, 'coldness': 2.0, 'realism': 3.5}},
        {"text": '马上。', "tags": {'concise': 5.0, 'coldness': 2.0, 'realism': 3.5}},
    ]),
    _q('pref_010', 'emotion', '推销电话："先生，我们有个理财推荐……"', '随便打发', [
        {"text": '不用了，谢谢。', "tags": {'emotion': 1.0, 'concise': 3.0, 'realism': 4.0}},
        {"text": '挂了，别打了。', "tags": {'emotion': 3.0, 'concise': 3.0, 'realism': 4.0}},
        {"text": '没兴趣。', "tags": {'emotion': 1.5, 'concise': 3.0, 'realism': 4.0}},
        {"text": '在忙。', "tags": {'emotion': 1.5, 'coldness': 3.0, 'concise': 3.0, 'realism': 4.0}},
    ]),
    _q('pref_011', 'emotion', '对象说："你根本不在乎我。"', '认真回', [
        {"text": '我在不在乎，你心里清楚。', "tags": {'emotion': 2.0, 'coldness': 3.0, 'realism': 4.5, 'concise': 3.0}},
        {"text": '你这么说，是想让我哄你吗？', "tags": {'emotion': 4.0, 'coldness': 1.0, 'realism': 4.0, 'concise': 3.0}},
        {"text": '那你说，怎么做才算在乎你？', "tags": {'emotion': 3.0, 'coldness': 1.0, 'realism': 4.5, 'concise': 3.0}},
        {"text": '我在不在乎，不在嘴上，在你心里有没有数。', "tags": {'emotion': 2.5, 'coldness': 2.5, 'realism': 4.5, 'concise': 3.0}},
    ]),
    _q('pref_012', 'retort', '同事甩锅："这个错是你犯的吧。"', '别背锅', [
        {"text": '不是我犯的，你别乱甩。', "tags": {'aggression': 2.0, 'oral': 4.0, 'concise': 3.0}, "retort": 'direct'},
        {"text": '你哪只眼睛看到是我犯的？', "tags": {'aggression': 2.0, 'oral': 4.0, 'concise': 3.0}, "retort": 'rhetorical'},
        {"text": '你确定？要不要再想想再说话？', "tags": {'aggression': 1.5, 'oral': 4.0, 'concise': 3.0}, "retort": 'rhetorical'},
        {"text": '错我认，不是我的错别往我头上按。', "tags": {'aggression': 2.5, 'oral': 4.0, 'concise': 3.0}, "retort": 'direct'},
    ]),
    _q('pref_013', 'retort', '朋友阴阳："你这品味，也就这样了。"', '回敬', [
        {"text": '嗯，毕竟不是谁都能有你这高级品味。', "tags": {'sarcasm': 3.0, 'aggression': 2.0, 'concise': 3.0}, "retort": 'disdain'},
        {"text": '是是是，您品味最高，别人都俗。', "tags": {'sarcasm': 3.0, 'aggression': 2.0, 'concise': 3.0}, "retort": 'tit_for_tat'},
        {"text": '你品味这么高，怎么没见你穿出个人样来。', "tags": {'sarcasm': 2.0, 'aggression': 3.5, 'concise': 3.0}, "retort": 'tit_for_tat'},
        {"text": '品味这东西，你懂多少？', "tags": {'sarcasm': 2.0, 'aggression': 2.0, 'concise': 3.0}, "retort": 'rhetorical'},
    ]),
    _q('pref_014', 'retort', '网络评论："你懂个屁就出来说。"', '别吵架但要有理', [
        {"text": '我懂不懂不用你证明，你倒是说说哪里错了。', "tags": {'aggression': 2.0, 'realism': 4.0, 'concise': 3.0}, "retort": 'logic'},
        {"text": '你上来就骂人，是没别的本事了吗？', "tags": {'aggression': 2.0, 'realism': 4.0, 'concise': 3.0}, "retort": 'direct'},
        {"text": '你倒是说说我哪句说错了，说对了算你赢。', "tags": {'aggression': 1.5, 'realism': 4.0, 'concise': 3.0}, "retort": 'logic'},
        {"text": '骂人之前，先把你自己的理讲清楚。', "tags": {'aggression': 2.0, 'realism': 4.0, 'concise': 3.0}, "retort": 'direct'},
    ]),
    _q('pref_015', 'fact', '同事说："你今天迟到了。"', '别认怂', [
        {"text": '是迟到了，但也没耽误你的事吧。', "tags": {'aggression': 2.0, 'realism': 4.0, 'concise': 3.0}, "fact": 'admit_then_retort'},
        {"text": '先别说我，你昨天的报告改完了吗？', "tags": {'aggression': 2.0, 'realism': 4.0, 'concise': 3.0}, "fact": 'deflect'},
        {"text": '迟到是我的事，你操心你自己吧。', "tags": {'aggression': 2.0, 'realism': 4.0, 'concise': 3.0}, "fact": 'deflect'},
        {"text": '我是迟到了，你是来替我打卡的吗？', "tags": {'aggression': 2.5, 'realism': 4.0, 'concise': 3.0}, "fact": 'admit_then_retort'},
    ]),
    _q('pref_016', 'fact', '亲戚说："你这胖得该减肥了。"', '不想被管', [
        {"text": '你操心你自己血压吧，我胖不胖心里有数。', "tags": {'aggression': 2.0, 'sarcasm': 2.0, 'realism': 4.0, 'concise': 3.0}, "fact": 'deflect'},
        {"text": '我胖我的，碍着你了？', "tags": {'aggression': 2.5, 'sarcasm': 2.0, 'realism': 4.0, 'concise': 3.0}, "fact": 'attack_behavior'},
        {"text": '胖不胖是我的事，你倒是管得宽。', "tags": {'aggression': 2.5, 'sarcasm': 2.0, 'realism': 4.0, 'concise': 3.0}, "fact": 'deflect'},
        {"text": '您先把自己血压降下来，再来管我胖不胖。', "tags": {'aggression': 2.5, 'sarcasm': 2.0, 'realism': 4.0, 'concise': 3.0}, "fact": 'attack_behavior'},
    ]),
    _q('pref_017', 'boundary', '朋友嘲笑："你连这都不会？"', '给他点颜色', [
        {"text": '会的人多了，你去找会的去。', "tags": {'boundary': 2.0, 'aggression': 2.0, 'oral': 4.0, 'concise': 3.0}},
        {"text": '你倒是会，会的东西也没见你拿出来过。', "tags": {'boundary': 4.0, 'aggression': 3.0, 'oral': 4.0, 'concise': 3.0}},
        {"text": '我不会的多了，你会的就那么几样。', "tags": {'boundary': 3.0, 'aggression': 2.5, 'oral': 4.0, 'concise': 3.0}},
        {"text": '你会的这么厉害，怎么没见你靠这个吃饭。', "tags": {'boundary': 4.0, 'aggression': 3.5, 'oral': 4.0, 'concise': 3.0}},
    ]),
    _q('pref_018', 'boundary', '陌生人挑衅："看什么看？"', '别惹事但要顶回去', [
        {"text": '看你长得好看。', "tags": {'boundary': 1.0, 'sarcasm': 3.0, 'emotion': 1.0, 'concise': 3.0}},
        {"text": '你管我看不看？路是你家的？', "tags": {'boundary': 4.0, 'aggression': 3.0, 'emotion': 3.0, 'concise': 3.0}},
        {"text": '你不看我，怎么知道我在看你？', "tags": {'boundary': 1.5, 'sarcasm': 2.5, 'emotion': 1.5, 'concise': 3.0}},
        {"text": '看你一眼又怎么了？', "tags": {'boundary': 2.0, 'aggression': 2.0, 'emotion': 2.0, 'concise': 3.0}},
    ]),
    _q('pref_019', 'realism', '同事说："你方案写得不行。"', '正常回', [
        {"text": '感谢你的反馈，我会认真考虑并改进。', "tags": {'realism': 1.0, 'concise': 3.0}},
        {"text": '行，你说说哪不行，我改。', "tags": {'realism': 5.0, 'concise': 3.0}},
        {"text": '那你给我指指，哪里不行。', "tags": {'realism': 4.5, 'concise': 3.0}},
        {"text": '行，我看看。', "tags": {'realism': 4.5, 'concise': 4.0}},
    ]),
    _q('pref_020', 'realism', '客服说："您的问题已记录，会尽快处理。"', '不满意', [
        {"text": '好的，麻烦您尽快处理，谢谢。', "tags": {'realism': 1.0, 'emotion': 1.0, 'concise': 3.0}},
        {"text": '我等的不是记录，是解决。', "tags": {'realism': 5.0, 'emotion': 2.0, 'concise': 3.0}},
        {"text": '什么时候能处理？给个时间。', "tags": {'realism': 4.5, 'emotion': 2.0, 'concise': 3.0}},
        {"text": '记录完然后呢？', "tags": {'realism': 4.5, 'emotion': 2.5, 'concise': 3.0}},
    ]),
    _q('pref_021', 'realism', '长辈说："年轻人要懂得忍让。"', '不想听大道理', [
        {"text": '您说得对，忍让是美德，我记下了。', "tags": {'realism': 2.0, 'concise': 3.0}},
        {"text": '忍让可以，但不能什么都忍。', "tags": {'realism': 4.0, 'concise': 3.0}},
        {"text": '忍让也得看对谁。', "tags": {'realism': 4.0, 'concise': 3.0}},
        {"text": '我懂，您放心。', "tags": {'realism': 3.5, 'concise': 3.0}},
    ]),
    _q('pref_022', 'realism', '同学说："你这么努力也没见考多好。"', '回怼', [
        {"text": '你行你上啊，不行别哔哔。', "tags": {'realism': 2.0, 'aggression': 3.0, 'oral': 3.0, 'concise': 3.0}},
        {"text": '我努力我的，你管好你自己就行。', "tags": {'realism': 4.5, 'aggression': 2.0, 'oral': 4.0, 'concise': 3.0}},
        {"text": '你倒是没努力，也没见你考多好。', "tags": {'realism': 4.0, 'aggression': 3.0, 'oral': 4.0, 'concise': 3.0}},
        {"text": '我考多少是我的事，你这么关注我成绩，是闲的？', "tags": {'realism': 4.0, 'aggression': 2.5, 'oral': 4.0, 'concise': 3.0}},
    ]),
    _q('pref_023', 'coldness', '对象问："你晚上干嘛去了？"', '不想说太多', [
        {"text": '跟朋友吃饭去了。', "tags": {'coldness': 1.0, 'oral': 4.0, 'concise': 3.0}},
        {"text": '吃饭。', "tags": {'coldness': 3.0, 'oral': 4.0, 'concise': 5.0}},
        {"text": '出去了一趟。', "tags": {'coldness': 2.0, 'oral': 4.0, 'concise': 3.0}},
        {"text": '有事。', "tags": {'coldness': 3.0, 'oral': 4.0, 'concise': 5.0}},
    ]),
    _q('pref_024', 'aggression', '室友说："你半夜打游戏吵死了。"', '不想吵但也不爽', [
        {"text": '行，我戴耳机。', "tags": {'aggression': 1.0, 'emotion': 2.0, 'concise': 3.0}},
        {"text": '你半夜打电话的时候，也没见你自觉。', "tags": {'aggression': 3.0, 'emotion': 3.0, 'concise': 3.0}, "retort": 'tit_for_tat'},
        {"text": '那你半夜动静大的时候，我说过你吗？', "tags": {'aggression': 2.5, 'emotion': 2.5, 'concise': 3.0}, "retort": 'rhetorical'},
        {"text": '行，以后我小声点，你也别双标。', "tags": {'aggression': 2.0, 'emotion': 2.5, 'concise': 3.0}, "retort": 'direct'},
    ]),
    _q('pref_025', 'sarcasm', '同事说："你效率真高啊。"（反话）', '阴阳回去', [
        {"text": '那可不，跟你学的。', "tags": {'sarcasm': 2.0, 'aggression': 1.0, 'oral': 4.0, 'concise': 3.0}},
        {"text": '那可不，主要是您衬托得好。', "tags": {'sarcasm': 4.0, 'aggression': 1.0, 'oral': 4.0, 'concise': 3.0}},
        {"text": '您这话，我怎么听着像夸我呢？', "tags": {'sarcasm': 3.0, 'aggression': 1.0, 'oral': 4.0, 'concise': 3.0}},
        {"text": '是啊，跟您比可不就高嘛。', "tags": {'sarcasm': 3.5, 'aggression': 1.5, 'oral': 4.0, 'concise': 3.0}},
    ]),
    _q('pref_026', 'concise', '朋友问："你周末怎么不出去玩？"', '应付一下', [
        {"text": '没空。', "tags": {'concise': 5.0, 'coldness': 2.0, 'realism': 4.0}},
        {"text": '没空，这周事多，下周再说吧。', "tags": {'concise': 3.0, 'coldness': 1.0, 'realism': 4.5}},
        {"text": '不想出去。', "tags": {'concise': 5.0, 'coldness': 2.0, 'realism': 4.0}},
        {"text": '在家歇着挺好。', "tags": {'concise': 4.0, 'coldness': 1.5, 'realism': 4.5}},
    ]),
    _q('pref_027', 'oral', '亲戚问："谈对象了没？"', '不想回答但别尴尬', [
        {"text": '没呢，您有合适的给我介绍介绍呗。', "tags": {'oral': 4.0, 'realism': 4.0, 'concise': 3.0}},
        {"text": '暂时还没有这个打算，随缘吧。', "tags": {'oral': 2.0, 'realism': 3.0, 'concise': 3.0}},
        {"text": '这不等着您给介绍嘛。', "tags": {'oral': 4.0, 'realism': 4.0, 'concise': 3.0}},
        {"text": '缘分还没到。', "tags": {'oral': 3.0, 'realism': 3.5, 'concise': 3.0}},
    ]),
    _q('pref_028', 'emotion', '领导说："这个项目你没做好。"', '认但不想多说', [
        {"text": '是，我复盘一下。', "tags": {'emotion': 1.0, 'realism': 4.0, 'concise': 3.0}},
        {"text": '嗯。', "tags": {'emotion': 2.0, 'coldness': 4.0, 'realism': 4.0, 'concise': 4.0}},
        {"text": '是，我看看问题出在哪。', "tags": {'emotion': 1.0, 'realism': 4.0, 'concise': 3.0}},
        {"text": '收到。', "tags": {'emotion': 1.5, 'coldness': 3.0, 'realism': 3.5, 'concise': 4.0}},
    ]),
    _q('pref_029', 'retort', '陌生人说："你这车也太破了吧。"', '不想理但要说一句', [
        {"text": '关你什么事？', "tags": {'aggression': 2.0, 'concise': 3.0}, "retort": 'rhetorical'},
        {"text": '破车不碍您眼就行。', "tags": {'aggression': 1.0, 'sarcasm': 3.0, 'concise': 3.0}, "retort": 'disdain'},
        {"text": '你倒是挺关心我的车。', "tags": {'aggression': 1.0, 'sarcasm': 3.0, 'concise': 3.0}, "retort": 'disdain'},
        {"text": '车破不破，也不影响我开。', "tags": {'aggression': 1.0, 'coldness': 2.5, 'concise': 3.0}, "retort": 'aloof'},
    ]),
    _q('pref_030', 'fact', '对象说："你上次答应的事又忘了。"', '别吵架', [
        {"text": '先吃饭，这事儿我记着呢。', "tags": {'aggression': 1.0, 'realism': 4.0, 'concise': 3.0}, "fact": 'deflect'},
        {"text": '我忘了是我的错，你提醒我就行了，别翻旧账。', "tags": {'aggression': 1.5, 'realism': 4.0, 'concise': 3.0}, "fact": 'admit_then_retort'},
        {"text": '我记得，就是还没来得及办。', "tags": {'aggression': 1.0, 'realism': 4.0, 'concise': 3.0}, "fact": 'admit_then_retort'},
        {"text": '你一说我想起来了，明天就去办。', "tags": {'aggression': 0.5, 'realism': 4.5, 'concise': 3.0}, "fact": 'deflect'},
    ]),
]


def _ensure_dirs() -> None:
    os.makedirs(_prefs_dir(), exist_ok=True)


# ---------------------------------------------------------------------------
# 出题
# ---------------------------------------------------------------------------
def start_evaluation(round_no: int = 1, history_records: Optional[List[Dict]] = None) -> List[Dict]:
    """返回本轮题单。第一轮：全量 30 题（QUESTION_BANK）。
    第二轮起：使用全新题库 ROUND2_BANK（不再重复第一轮题目），按不确定维度定向排序（最不确定优先）。"""
    if round_no <= 1:
        return [dict(q) for q in QUESTION_BANK]
    prof = compute_profile(history_records or _load_records())
    order = _rank_uncertain_dims(prof)
    bank = ROUND2_BANK if round_no == 2 else ROUND3_BANK
    by_dim: Dict[str, List[Dict]] = {}
    for q in bank:
        by_dim.setdefault(q["dimension"], []).append(q)
    out: List[Dict] = []
    seen: set = set()
    for dim in order + [d for d in by_dim if d not in order]:
        for q in by_dim.get(dim, []):
            if q["test_id"] not in seen:
                seen.add(q["test_id"])
                out.append(dict(q))
        if len(out) >= 30:
            break
    return out[:30]


def _rank_uncertain_dims(prof: Dict) -> List[str]:
    """不确定度 = 样本少 / 方差大 → 优先出题；同分按 CONT_DIMS 固定顺序（稳定可预期）。"""
    conf = prof.get("dims", {})
    scored = []
    for i, d in enumerate(CONT_DIMS):
        c = conf.get(d, {}).get("confidence", 0.0)
        n = conf.get(d, {}).get("sample_count", 0)
        scored.append((d, 1.0 - c + (0 if n else 1.0), i))
    scored.sort(key=lambda x: (-x[1], x[2]))
    return [d for d, _, _ in scored]


# ---------------------------------------------------------------------------
# 记录
# ---------------------------------------------------------------------------
def record_choice(test_id: str, choice: str, answer_a: str = "", answer_b: str = "",
                  scenario: str = "", user_request: str = "",
                  dimension: str = "") -> None:
    """保存完整样本：场景 + 用户要求 + 全部候选 + 用户选择（A/B/C/D 或 都不好）。"""
    _ensure_dirs()
    q = _find_question(test_id)
    answers = q.get("answers") if q else None
    rec = {
        "test_id": test_id,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dimension": dimension or (q["dimension"] if q else ""),
        "scenario": scenario or (q["scenario"] if q else ""),
        "user_request": user_request or (q["user_request"] if q else ""),
        "answers": [a["text"] for a in answers] if answers else [answer_a, answer_b],
        "choice": choice,
        "relation": q.get("relation", "") if q else "",
    }
    with open(_records_file(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _find_question(test_id: str) -> Optional[Dict]:
    for q in QUESTION_BANK + ROUND2_BANK + ROUND3_BANK:
        if q["test_id"] == test_id:
            return q
    return None


def _load_records() -> List[Dict]:
    _ensure_dirs()
    if not os.path.exists(_records_file()):
        return []
    out = []
    with open(_records_file(), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


# ---------------------------------------------------------------------------
# 画像
# ---------------------------------------------------------------------------
def compute_profile(records: Optional[List[Dict]] = None) -> Dict[str, Any]:
    """从全部记录统计偏好画像。连续维度均值+置信度；分类维度频次；dislikes=都不好次数+反馈。"""
    records = records if records is not None else _load_records()
    dims: Dict[str, List[float]] = {d: [] for d in CONT_DIMS}
    retort_count: Dict[str, int] = {s: 0 for s in RETORT_STYLES}
    fact_count: Dict[str, int] = {s: 0 for s in FACT_STYLES}
    dislikes = 0
    for rec in records:
        choice = rec.get("choice", "").strip().upper()
        if choice == "反馈":
            _apply_feedback_signals(rec.get("signals") or {}, dims)
            continue
        q = _find_question(rec.get("test_id", ""))
        if not q:
            continue
        # 支持多选（如 "A,C"）：选中的每个候选都是有效样本
        for sel in choice.split(","):
            sel = sel.strip().upper()
            if sel in ("A", "B", "C", "D"):
                idx = ord(sel) - ord("A")
                answers = q.get("answers") or []
                if 0 <= idx < len(answers):
                    _accum(rec, q, answers[idx].get("tags", {}), dims, retort_count, fact_count)
                else:
                    dislikes += 1
            elif sel in ("都不好", "都不", "E", "无"):
                dislikes += 1
    profile: Dict[str, Any] = {"dims": {}, "retort_style": {}, "fact_handling": {}, "dislikes": dislikes}
    for d in CONT_DIMS:
        vals = dims[d]
        n = len(vals)
        profile["dims"][d] = {
            "value": round(sum(vals) / n, 2) if n else 0.0,
            "confidence": round(min(0.98, n / (n + 6)), 2),
            "sample_count": n,
        }
    total_retort = sum(retort_count.values())
    for s in RETORT_STYLES:
        profile["retort_style"][s] = round(retort_count[s] / total_retort, 2) if total_retort else 0.0
    total_fact = sum(fact_count.values())
    for s in FACT_STYLES:
        profile["fact_handling"][s] = round(fact_count[s] / total_fact, 2) if total_fact else 0.0
    profile["total_samples"] = len(records)
    profile["stability"] = round(min(0.98, sum(
        profile["dims"][d]["confidence"] for d in CONT_DIMS) / len(CONT_DIMS)), 2)
    return profile


def _accum(rec: Dict, q: Dict, tags: Dict, dims: Dict, retort_count: Dict, fact_count: Dict) -> None:
    for d in CONT_DIMS:
        v = tags.get(d)
        if v is not None:
            dims[d].append(float(v))
    choice = str(rec.get("choice", "")).strip().upper()[:1]
    answers = q.get("answers")
    if not answers:
        return
    idx = ord(choice) - ord("A")
    if 0 <= idx < len(answers):
        a = answers[idx]
        if a.get("retort") in retort_count:
            retort_count[a["retort"]] += 1
        if a.get("fact") in fact_count:
            fact_count[a["fact"]] += 1


def save_profile(profile: Dict) -> None:
    _ensure_dirs()
    with open(_profile_file(), "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)


def load_profile() -> Dict[str, Any]:
    """读取当前用户个人画像；不存在（新用户/已重置）→ 回落到 Skill 内置默认画像。
    个人画像存在但某维度样本过少（<5）时，该维度**维度级回落**到默认画像——
    防止"一两次选择就覆盖已验证的默认能力"（一次选择绝不构成规则）。"""
    personal = None
    if os.path.exists(_profile_file()):
        try:
            with open(_profile_file(), encoding="utf-8") as f:
                personal = json.load(f)
        except (json.JSONDecodeError, OSError):
            personal = None
    default = None
    dp = default_profile_path()
    if os.path.exists(dp):
        try:
            with open(dp, encoding="utf-8") as f:
                default = json.load(f)
        except (json.JSONDecodeError, OSError):
            default = None
    if personal is None:
        return default or {}
    if default is None:
        return personal
    # 维度级回落：个人画像中"有 sample_count 且样本不足(<5)"的维度，用默认画像值覆盖——
    # 防止"一两次选择就覆盖已验证的默认能力"（一次选择绝不构成规则）。
    # 外部注入的完整画像（无 sample_count 字段）视为已验证，直接使用。
    merged = dict(personal)
    pdims = (personal.get("dims") or {})
    ddims = (default.get("dims") or {})
    mdims = dict(pdims)
    for dim, dval in ddims.items():
        pval = pdims.get(dim)
        if isinstance(pval, dict) and "sample_count" in pval and pval["sample_count"] < 5:
            mdims[dim] = dict(dval)  # 回落默认（含 confidence）
    merged["dims"] = mdims
    merged["is_default"] = False
    return merged


# ---------------------------------------------------------------------------
# 反馈信号（正常使用中的隐式反馈）
# ---------------------------------------------------------------------------
def apply_feedback(text: str) -> Dict[str, Any]:
    """"太客气了 / 太狠了 / 太长 / 不像人话" 等 → 偏好信号，落一条反馈记录并重算画像。"""
    signals = {}
    for item in FEEDBACK_SIGNALS:
        if any(k in text for k in item["keys"]):
            for dim, delta in item["signals"].items():
                signals[dim] = signals.get(dim, 0.0) + delta
    _ensure_dirs()
    rec = {
        "test_id": "feedback",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dimension": "feedback",
        "scenario": "",
        "user_request": text,
        "answer_a": "", "answer_b": "",
        "choice": "反馈",
        "signals": signals,
    }
    with open(_records_file(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    profile = compute_profile(_load_records())
    save_profile(profile)
    return signals


def _apply_feedback_signals(signals: Dict[str, float], dims: Dict[str, List[float]]) -> None:
    for dim, delta in (signals or {}).items():
        if dim in dims:
            base = sum(dims[dim]) / len(dims[dim]) if dims[dim] else 2.5
            dims[dim].append(max(0.0, min(5.0, base + delta)))


# ---------------------------------------------------------------------------
# 测评摘要（供 30 题结束后展示；只显示结论，不显示内部题解）
# ---------------------------------------------------------------------------
def evaluation_summary(records: Optional[List[Dict]] = None) -> Dict[str, Any]:
    records = records if records is not None else _load_records()
    prof = compute_profile(records)
    d = prof["dims"]
    clear_like = []
    clear_dislike = []
    if d["concise"]["value"] >= 3.5 and d["concise"]["sample_count"] >= 3:
        clear_like.append("简短")
    if d["oral"]["value"] >= 3.5 and d["oral"]["sample_count"] >= 3:
        clear_like.append("真人口语")
    if d["realism"]["value"] >= 3.5 and d["realism"]["sample_count"] >= 3:
        clear_like.append("真人感强的表达")
    if d["sarcasm"]["value"] <= 2.0 and d["sarcasm"]["sample_count"] >= 3:
        clear_dislike.append("明显阴阳")
    if d["aggression"]["value"] >= 3.5 and d["aggression"]["sample_count"] >= 3:
        clear_like.append("较强攻击")
    if d["coldness"]["value"] >= 3.5 and d["coldness"]["sample_count"] >= 3:
        clear_like.append("偏冷")
    if prof["dislikes"] > 0:
        clear_dislike.append(f"有 {prof['dislikes']} 题两个都不满意")
    unsure = []
    for dim, why in (("sarcasm", "阴阳 vs 直接"), ("aggression", "中等 vs 强攻击"),
                     ("coldness", "冷 vs 不冷")):
        c = d.get(dim, {}).get("confidence", 0)
        if c < 0.6:
            unsure.append(why)
    return {
        "total_samples": len(records),
        "stability": prof["stability"],
        "like": clear_like,
        "dislike": clear_dislike,
        "unsure": unsure,
        "dims": {k: {"value": v["value"], "confidence": v["confidence"]} for k, v in d.items()},
    }


def _pretty_summary(s: Dict) -> str:
    dims = s["dims"]
    lines = [
        f"本轮样本：{s['total_samples']} 题 | 偏好稳定度：{int(s['stability']*100)}%",
        "当前偏好：",
    ]
    for k, v in dims.items():
        lines.append(f"  {k}: {v['value']:.1f}/5（置信 {int(v['confidence']*100)}%）")
    if s["like"]:
        lines.append("明确偏好：" + "、".join(s["like"]))
    if s["dislike"]:
        lines.append("明确反感：" + "、".join(s["dislike"]))
    if s["unsure"]:
        lines.append("尚不确定：" + "、".join(s["unsure"]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    import sys
    args = sys.argv[1:]
    cmd = args[0] if args else "help"
    if cmd == "start":
        r = args[1] if len(args) > 1 else "1"
        try:
            r = int(r)
        except ValueError:
            r = 1
        qs = start_evaluation(r)
        print(f"# 第 {r} 轮测评（{len(qs)} 题）")
        for q in qs:
            print(f"{q['test_id']} [{q['dimension']}] {q['scenario']} | 要求：{q['user_request']}")
            for i, a in enumerate(q.get("answers", [])):
                print(f"  {chr(ord('A')+i)}: {a['text']}")
    elif cmd == "record":
        # record <test_id> <A|B|都不好>
        if len(args) >= 3:
            record_choice(args[1], args[2].upper())
            print("recorded")
    elif cmd == "profile":
        prof = compute_profile()
        save_profile(prof)
        print(_pretty_summary(evaluation_summary()))
    elif cmd == "feedback":
        if len(args) >= 2:
            sig = apply_feedback(args[1])
            print("signals:", sig)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
