---
case_id: xianzou-你先走
canonical_trigger: 同事说'别人都没走你就先走了'
intent: 回应别人指责你提前下班/不合群
scope: scene_specific
scene_constraints: ["职场"]
relation_constraints: ["同事"]
keywords: [下班, 先走, 没走, 卡点, 合群]
status: verified
tone: 冷
strength: 中
strategy_refs: ["shift-onto-them", "aloof"]
---

## user_query_examples
- 同事说我走得太早怎么回
- 别人说你提前下班怎么办
- 说我不合群先走怎么怼
- 领导说我走得早

## response
- 那你让他们都走呗。

## usage
不解释自己为什么走，把'大家都没走'这个指责反手推回给对方。
