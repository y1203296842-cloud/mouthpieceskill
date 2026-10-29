"""adapters/host/trae.py — Trae Host Adapter（格式壳）。

Trae 通过 Agent Skill / 指令技能机制加载技能。具体布局以 Trae 当前文档为准；
本模块保留一个"如何打包"的说明，改动只发生在这里，不影响 Core。
"""
from __future__ import annotations


def pack_instructions() -> str:
    return (
        "Trae：将 Core 包复制到 Trae 的技能目录，并在技能 manifest 中声明入口 SKILL.md。\n"
        "若 Trae 要求特定 front-matter 字段，仅在本 Adapter 增加映射，不改 Core。\n"
        "【KB 调用入口（强制，Trae 无内置 Bash 时用其工具执行脚本）】用户提嘴替问题时，\n"
        "  python3 <项目根>/scripts/retrieve.py \"<用户问题>\" --json\n"
        "  一次拿答案+思路+风格后再生成；若 Trae 环境没有 Python，用它的命令执行器运行同一条命令。\n"
        "  若 Trae 无法执行脚本（KB_CALLED=false），必须如实按无知识库 fallback，不得假装已检索。\n"
    )


def validate(root: str) -> list:
    from . import validate_core_is_portable
    import os
    problems = validate_core_is_portable(root)
    if not os.path.exists(os.path.join(root, "scripts", "retrieve.py")):
        problems.append("缺少统一检索入口 scripts/retrieve.py")
    return problems
