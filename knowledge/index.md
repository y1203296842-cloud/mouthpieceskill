---
knowledge_version: 2
schema_version: 2
updated: 2026-10-04
---

# knowledge/index.md — 知识库概述

> **检索索引由脚本自动生成**（`knowledge/index/case_manifest.json` + `keyword_index.json`），
> 本文件只是人工可读的概述，不参与程序检索。新增知识后自动重建索引，无需人工维护。

## 案例一览（21 个，v3 通用性重判）
scope 统计：broad=10 / domain_specific=8 / relational=3（均无来源字段；broad 案例 trigger 已泛化为"别人/对象"并带跨关系问法）
| case_id | 触发语（用户会怎么问） | scope | 限定 |
|---|---|---|---|
| reject-tuoyanni | 别人/同事说很讨厌你 | broad | — |
| taimingan-太敏感 | 别人说你太敏感/想太多/玻璃心 | broad | — |
| guanxi-靠关系 | 别人内涵/说你靠关系、关系户 | broad | — |
| kadian-卡点下班 | 同事说你卡点下班 | domain_specific | 职场 |
| buxihuanni-领导不喜欢 | 领导说不喜欢你 | relational | 领导 |
| rangzuo-让座 | 陌生人/长辈指责你没让座 | relational | 陌生人/长辈 |
| shengao-喜欢高的 | 说喜欢高的被嘲笑身高 | broad | — |
| yidubuhui-已读不回 | crush 已读不回 | domain_specific | 情感 |
| zhaohua-找话 | crush 不说话/冷场 | domain_specific | 情感 |
| haolei-说好累 | 对象/crush 说好累 | domain_specific | 情感 |
| chicu-吃醋 | crush 故意让你吃醋 | domain_specific | 情感 |
| hong-哄冷淡对象 | 对象冷淡了想哄回来 | domain_specific | 情感 |
| fenshou-说分手 | 对象突然说分手 | domain_specific | 情感 |
| buhequn-不合群 | 别人说你不合群/不会做人 | broad | — |
| nijile-你急了 | 别人说你急了 | broad | — |
| tuodi-使唤你干活 | 别人使唤你干活/跑腿 | relational | 同事/室友 |
| bingui-比你贵 | 别人攀比说自己比你贵 | broad | — |
| daxuesheng-亏你还是大学生 | 别人说你学历/读书没用 | broad | — |
| cuihun-亲戚催婚 | 亲戚过年催婚/盘问 | relational | 亲戚 |
| paimapi-升职靠拍马屁 | 同事说你升职靠拍马屁 | domain_specific | 职场 |
| huotuichang-喂狗 | 别人阴阳你 | broad | — |

## 策略一览
见 `knowledge/strategies/`：reverse-premise（不接前提）· shift-onto-them（引回对方）·
play-dumb（装傻）· ironic（阴阳）· aloof（高冷）· absurd（荒诞）· flirt（撩）· comfort（安抚）。

## 数据溯源（不在知识库内）
来源信息只存于 `raw/`（开发环境保留原始投喂源与来源映射，不随发布包分发）：`raw/case_sources.json`（case→来源映射）、
`raw/cases_v1/`（旧 schema 案例存档）、`raw/creators/`（博主画像）。
