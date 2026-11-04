# references/hosting.md — 跨宿主 Skill 格式与 Host Adapter

> 目标：核心逻辑与具体 Agent 解耦，做成可移植标准 Skill；各宿主差异只做薄 Adapter，不重写 Core。
> 原则：**Core + Portable Skill Package + Host Adapter**。若各平台最终支持同一种标准 Skill 格式，优先用标准格式；有细微差异才单独适配。

## 一、宿主能力与格式（截至 v1.0 调研）

| 宿主 | Skill/指令机制 | 说明 |
|---|---|---|
| Claude Code (CC) | Skills（`.claude/skills/<name>/SKILL.md` + 目录），Agent 按需加载 | 标准目录格式，天然适合本 Skill |
| OpenAI Codex | Agent Skills（`SKILL.md`）；全局 `~/.agents/skills/<name>/` 与 `~/.codex/skills/<name>/`，项目级 `.agents/skills/` 与 `.codex/skills/`；只读 front-matter 的 `name`/`description` 判定触发 | 已用 junction 单真身接入（见第五节），Core 无需改动 |
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

## 五、Windows 双平台单真身接入（Codex × Doubao Work）

**目标**：同一台 Windows 机器上，Codex 与豆包侧读写**同一份** Skill 文件，任一平台改动对另一平台即时可见；历史与备份交给 git。

```
真身（唯一事实来源，含 .git）：
  <豆包技能根>\.user_skills\personal-mouthpiece
        ↑ junction
  ~/.agents/skills/personal-mouthpiece        （Codex 全局，.agents 布局）
  ~/.codex/skills/personal-mouthpiece         （Codex 全局，.codex 布局）
```

- 建链（管理员 CMD）：`mklink /J "<路径>" "<真身绝对路径>"`；PowerShell 用 `New-Item -ItemType Junction -Path "<路径>" -Target "<真身>"`。
- 绝不要给 Codex 侧放独立副本：多副本会版本漂移（实测旧副本停在 v1.1.0，SKILL.md 与真身 hash 不一致）。替换前先备份旧副本并逐文件比对 `memory/`，确认无独有数据。
- **脚本入口必须用绝对路径**：Codex 的 CWD 不一定是技能根，`python3 scripts/retrieve.py` 可能找不到文件；统一用
  `python3 "<技能根>/scripts/retrieve.py" "<用户问题>" --json`。
- 同步纪律：
  - 日常修改：任一平台改完即另一平台可见（物理同一份），无需 pull。
  - 提交：`git add -A && git commit -m "..." && git push`（remote 已配 GCM，免登录）。
  - 若另一平台在别的机器上工作：该机 clone 同一 remote，用 `git pull --rebase` 后再改，避免冲突。
- 版本一致性自检：比对两侧 `SKILL.md` 的 SHA256，一致即未漂移。
