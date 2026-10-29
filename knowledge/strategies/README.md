# strategies/ —— 迁移说明（已由 moves/ 招式库取代）

> 现状：`knowledge/strategies/` 里的 8 个 strategy 是早期"动作+语气混在一起"的粗分类。
> 凌晨《招式体系改造方案》已将其**重新归类进 `knowledge/moves/`（9 招式 + 2 目标模式）**。
> 本目录文件仅作**旧模式兼容**（供 `retrieve.py` 的 patterns 兜底引用），新增生成以 `moves/` 为准。

## 8 个 strategy → moves 映射
| 旧 strategy | 判定 | 去向 |
|---|---|---|
| 不接前提 | 真·招式（守势总纲） | → `moves/m1-bujie.md` |
| 反打回 | 真招式但太粗 | 拆 → `moves/m2-fanwen.md`（反问）+ `moves/m3-kouhui.md`（扣回·诛心） |
| 阴阳 | 是语气不是招式 | 动作内核 → `moves/m4-pengsha.md`（捧杀）；"阴阳"作为语气标签 |
| 装傻 | 真·招式 | → `moves/m6-zhuangsha.md` |
| 高冷 | 是语气不是招式 | 动作内核 → `moves/m7-lengchuli.md`（冷处理）；"高冷"作为语气标签 |
| 荒诞 | 真·招式 | → `moves/m5-huandanhua.md` |
| 转移 | 招式子集，不独立 | 并入 反问/装傻/荒诞 |
| 安抚 | 目标模式 | → `moves/g1-guanshu.md`（关心·接住情绪） |

## 新增招式
捧杀(M4)、立边界(M8)、自嘲接梗(M9)、撩·暧昧推拉(G2)。

## 约定
- **招式 = 做什么**（可迁移方法）；**语气 = 怎么说**（正交维度）；**强度 1-5 = 多狠**；**目标模式 = 这场要达成什么**。
- 后续喂料优先补弱招式样本：立边界 / 关心 / 自嘲 / 撩。
