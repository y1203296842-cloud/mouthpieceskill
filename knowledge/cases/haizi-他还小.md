---
case_id: haizi-他还小
canonical_trigger: 公共场所孩子吵/闹，你嫌吵，对方用'他还是个孩子'护短，或家长在场却不制止
intent: 回击'他还是个孩子'式护短、家长不作为
scope: scene_specific
scene_constraints: ["公共交通","公共场所"]
relation_constraints: ["陌生人","路人"]
keywords: [孩子, 吵, 嫌吵, 他还小, 他还是个孩子, 一等座, 熊孩子, 家长, 护短]
status: verified
tone: 阴阳
strength: 高
strategy_refs: ["attack-omission", "reverse-premise", "ironic"]
---

## user_query_examples
- 高铁上孩子吵，我说嫌吵，对方说他还小怎么怼
- 对方说嫌吵你去坐一等座呀，他还是个孩子怎么回
- 公共场所熊孩子吵，家长拿孩子当挡箭牌怎么怼
- 孩子吵家长不管还护短怎么回

## response
- 你不说话的时候，我还以为他没娘呢。

## usage
不攻击孩子，攻击'家长在场却不制止'的不作为——'他没娘'是说家长有嘴不用、光装哑巴。别接'孩子该被忍'的前提，也别接'嫌吵你该走'的逻辑。专治拿孩子当免罪金牌的人。
