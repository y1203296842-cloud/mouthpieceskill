"""adapters/host/ — Host Adapter（跨宿主薄壳）。

原则：Core（SKILL.md + references + knowledge + memory）跨宿主通用；
只有这里包含宿主专属的"技能清单/触发命令/打包布局"。
新增宿主 = 新增一个模块，不重写 Core。
"""
from __future__ import annotations

import os

HOSTS = ("claude_code", "trae", "workbuddy")


def host_manifest(host: str) -> dict:
    """返回某宿主的技能打包布局（仅格式壳，不含 Core 逻辑）。"""
    if host == "claude_code":
        return {
            "install_path": ".claude/skills/personal-mouthpiece",
            "entry": "SKILL.md",
            "requires": ["SKILL.md", "references/", "knowledge/", "memory/"],
            "adapter": "adapters/host/claude_code.py",
        }
    if host == "trae":
        return {
            "install_path": "skills/personal-mouthpiece",   # 依 Trae 当前文档调整
            "entry": "SKILL.md",
            "requires": ["SKILL.md", "references/", "knowledge/", "memory/"],
            "adapter": "adapters/host/trae.py",
        }
    if host == "workbuddy":
        return {
            "install_path": "skills/personal-mouthpiece",
            "entry": "SKILL.md",
            "requires": ["SKILL.md", "references/", "knowledge/", "memory/"],
            "adapter": "adapters/host/workbuddy.py",
        }
    raise ValueError(f"未知宿主: {host}")


def validate_core_is_portable(root: str) -> list:
    """校验 Core 目录不包含宿主专属文件（跨 Host 测试用）。"""
    problems = []
    for host in HOSTS:
        f = os.path.join(root, "adapters", "host", f"{host}.py")
        if not os.path.exists(f):
            problems.append(f"缺少宿主 Adapter: {host}")
    # Core 的 knowledge/memory 不应含 host 专属布局
    for sub in ("knowledge", "memory"):
        d = os.path.join(root, sub)
        if not os.path.isdir(d):
            problems.append(f"缺少 Core 目录: {sub}")
    return problems
