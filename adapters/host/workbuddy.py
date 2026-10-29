"""adapters/host/workbuddy.py — WorkBuddy Host Adapter（格式壳）。

WorkBuddy 支持指令技能 / Agent Skill。具体格式以 WorkBuddy 当前文档为准；
本模块保留打包说明，改动只发生在这里，不影响 Core。
"""
from __future__ import annotations


def pack_instructions() -> str:
    return (
        "WorkBuddy：将 Core 包放入技能目录，入口为 SKILL.md。\n"
        "若 WorkBuddy 需要专属描述字段，仅在本 Adapter 增加映射，不改 Core。\n"
        "【KB 调用入口（强制）】用户提嘴替问题时，先执行一次：\n"
        "  python3 <项目根>/scripts/retrieve.py \"<用户问题>\" --json\n"
        "  一次拿答案+思路+风格后再生成；若 WorkBuddy 有文件/工具接入，把 scripts/ 与 knowledge/ 注册为资源。\n"
    )


def validate(root: str) -> list:
    from . import validate_core_is_portable
    import os
    problems = validate_core_is_portable(root)
    if not os.path.exists(os.path.join(root, "scripts", "retrieve.py")):
        problems.append("缺少统一检索入口 scripts/retrieve.py")
    return problems
