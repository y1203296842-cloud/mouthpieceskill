---
case_id: jiangjin-发奖金请客
canonical_trigger: 同事说你发奖金了不请客/阴阳你抠门
intent: 回应别人阴阳你发奖金不请客
scope: scene_specific
scene_constraints: ["职场"]
relation_constraints: ["同事"]
keywords: [奖金, 请客, 抠门, 工资]
status: verified
tone: 犀利
strength: 中
strategy_refs: ["shift-onto-them", "ironic"]
---

## user_query_examples
- 同事说我发奖金不请客怎么回
- 别人阴阳我抠门怎么办
- 被说请不起客怎么怼
- 同事催我请吃饭

## response
- 主要是你也没拿过奖金，我怕你回请不起。

## usage
点破对方没拿过奖金、请不起，把'抠门'这个帽子反扣回去。
