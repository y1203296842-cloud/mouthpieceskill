"""adapters/host/codex.py — OpenAI Codex Host Adapter（格式壳）。

Codex 原生支持 Agent Skills：读取 <skill>/SKILL.md 的 front-matter（仅 name/description）
决定何时触发，正文在触发后才加载。技能目录两种布局均有实际使用：
  - 全局：~/.agents/skills/<name>/  与  ~/.codex/skills/<name>/
  - 项目级：<project>/.agents/skills/<name>/  与  .codex/skills/<name>/

本机接入方式：两个全局路径均用 Windows junction 指向同一真身（豆包侧技能根），
Codex 与豆包读写同一份文件，任一平台修改对另一平台即时可见；git remote 负责历史与备份。

本模块只提供"如何把 Core 包成 Codex 技能"的说明与校验，不含任何 Core 逻辑。
"""
from __future__ import annotations


def pack_instructions() -> str:
    return (
        "Codex 接入（Windows 单真身方案）：\n"
        "  ~/.agents/skills/personal-mouthpiece 与 ~/.codex/skills/personal-mouthpiece\n"
        "  均以 junction 指向同一技能根目录，避免多副本漂移。\n"
        "  Codex 只从 SKILL.md front-matter 读取 name/description 判定触发，无需额外 manifest。\n"
        "【KB 调用入口（强制）】Codex 的工作目录不一定是技能根，调用脚本必须用绝对路径：\n"
        "  python3 \"<技能根>/scripts/retrieve.py\" \"<用户问题>\" --json\n"
        "  → 拿答案候选+回应思路+风格规范后，再按 SKILL.md Step3/4 生成；禁止自己遍历 cases 目录。\n"
        "【知识写入】Codex 侧投喂/清洗数据仍写同一 knowledge/ 与 memory/，无需额外同步步骤。\n"
    )


def validate(root: str) -> list:
    # 复用通用校验：Core 目录齐全 + SKILL.md 存在 + 检索入口存在
    from . import validate_core_is_portable
    import os
    problems = validate_core_is_portable(root)
    if not os.path.exists(os.path.join(root, "SKILL.md")):
        problems.append("缺少 SKILL.md")
    if not os.path.exists(os.path.join(root, "scripts", "retrieve.py")):
        problems.append("缺少统一检索入口 scripts/retrieve.py")
    return problems
