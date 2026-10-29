# references/hosting.md — 跨宿主 Skill 格式与 Host Adapter

> 目标：核心逻辑与具体 Agent 解耦，做成可移植标准 Skill；各宿主差异只做薄 Adapter，不重写 Core。
> 原则：**Core + Portable Skill Package + Host Adapter**。若各平台最终支持同一种标准 Skill 格式，优先用标准格式；有细微差异才单独适配。

## 一、宿主能力与格式（截至 v1.0 调研）

| 宿主 | Skill/指令机制 | 说明 |
|---|---|---|
| Claude Code (CC) | Skills（`.claude/skills/<name>/SKILL.md` + 目录），Agent 按需加载 | 标准目录格式，天然适合本 Skill |
| Trae | Agent Skill / 指令技能机制（自定义技能目录 + 描述触发） | 支持类 Skill 包；用 Host Adapter 做格式壳 |
| WorkBuddy | 指令技能 / Agent Skill | 同样用 Host Adapter 做格式壳 |
| 其他（Doubao Work 等） | Skill 根目录 + SKILL.md | 本 Skill 本身就是该格式 |

> 注意：宿主机制在演进，实际以各宿主当前文档为准。**设计上不锁死任何一家。**

## 二、三层结构

```
personal-mouthpiece-skill/
├── SKILL.md                 # ← Portable Core（角色/工作流，跨宿主通用，不写宿主专属字段）
├── references/              # ← 规范（跨宿主通用）
├── knowledge/  memory/      # ← 真实数据（跨宿主通用）
└── adapters/host/           # ← Host Adapter（仅这里含宿主专属字段）
```

## 三、Host Adapter 说明

`adapters/host/` 下每个文件面向一个宿主，职责只做**格式壳**：
- 把 Core 的 `SKILL.md` 包装成该宿主可识别的技能清单（如 `.claude/skills/` 布局、Trae 技能 manifest、WorkBuddy 技能描述）。
- 把入口指令（`trigger`）映射到该宿主的关键词/命令机制。
- **不复制、不重写** Core 逻辑。

MVP 提供三个宿主 Adapter 的说明性文件 + 一个校验脚本，保证以后适配 CC/Trae/WorkBuddy 时**不推倒重来**。

## 四、兼容判断（写入测试）
- 测试会校验：Skill 包能明确区分 `Core / Host Adapter`，且 Core 目录不含宿主专属文件。
- 新增宿主 = 新增一个 `adapters/host/<name>/`，不动 Core。
