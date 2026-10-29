"""adapters/host/claude_code.py — Claude Code Host Adapter（格式壳）。

Claude Code 使用 Skills 机制：把整个项目放到 `.claude/skills/personal-mouthpiece/`。
Agent 会读取 SKILL.md（含 name/description/trigger），按需加载。
本模块只提供"如何把 Core 包成 CC 技能"的说明与校验，不包含任何 Core 逻辑。
"""
from __future__ import annotations


def pack_instructions() -> str:
    return (
        "Claude Code 安装：把本项目根目录复制到 `.claude/skills/personal-mouthpiece/`。\n"
        "SKILL.md 的 front-matter（name/description/trigger）即 CC 识别的技能清单。\n"
        "不需要额外配置；Agent 在需要时自动加载。\n"
        "【KB 调用入口（强制）】用户提嘴替问题时，先运行一次 Bash：\n"
        "  python3 <项目根>/scripts/retrieve.py \"<用户问题>\" --json\n"
        "  → 拿答案候选+回应思路+风格规范后，再按 SKILL.md Step3/4 生成；禁止自己遍历 cases 目录。\n"
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
