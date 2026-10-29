"""scripts/init.py — 安装自检与初始化（发布版安装入口）。

普通用户下载后运行一次：python3 scripts/init.py
做的事情：
  1. 环境检查（Python 版本 / 标准库依赖）
  2. 知识库完整性（cases / strategies / moves / 默认能力文件 / 索引）
  3. 自动重建缺失索引（不需要用户手工维护）
  4. 检索冒烟测试（内置示例问题，验证知识库真实可用）
  5. 用户记忆初始化（创建 memory/users/default/，确认可写）
  6. 输出 PASS / FAIL 与失败原因

不依赖开发者电脑的绝对路径；不包含任何密钥或凭证；可重复运行（幂等）。
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(f"{label}: {detail}")
    return ok


def main() -> int:
    print("=" * 52)
    print("  私人嘴替 Skill · 安装自检 v1.8.0")
    print("  开发者：抖音杨枝甘霖")
    print("=" * 52)

    print("\n[1/6] 环境检查")
    py = sys.version_info
    check("Python 3.8+", py >= (3, 8), f"当前 {py.major}.{py.minor}.{py.micro}")
    for mod in ("json", "os", "re", "glob", "shutil", "argparse", "time", "collections"):
        try:
            __import__(mod)
        except ImportError as e:
            check(f"标准库 {mod}", False, str(e))
    check("无需第三方依赖（纯标准库）", True, "")

    print("\n[2/6] 知识库完整性")
    from adapters.markdown_provider import MarkdownKnowledgeProvider
    kp = MarkdownKnowledgeProvider(ROOT)
    cases = kp.list_cases()
    check("案例库存在且非空", len(cases) >= 10, f"{len(cases)} 个 case")
    checked = sum(1 for c in cases if c.status == "verified")
    check("案例全部可检索（verified）", checked == len(cases), f"{checked}/{len(cases)}")

    strategies = kp.list_strategies()
    check("策略库存在", len(strategies) >= 5, f"{len(strategies)} 个策略")
    moves_dir = os.path.join(ROOT, "knowledge", "moves")
    n_moves = len([f for f in os.listdir(moves_dir) if f.endswith(".md")]) if os.path.isdir(moves_dir) else 0
    check("招式库存在", n_moves >= 8, f"{n_moves} 个招式/目标模式")

    dp = os.path.join(ROOT, "knowledge", "style", "default_profile.json")
    check("默认画像存在（新用户自动继承）", os.path.exists(dp), "knowledge/style/default_profile.json")
    lessons = os.path.join(ROOT, "knowledge", "style", "lessons.md")
    check("长期经验存在（生成时参照）", os.path.exists(lessons), "knowledge/style/lessons.md")

    print("\n[3/6] 索引状态")
    idx_manifest = os.path.join(ROOT, "knowledge", "index", "case_manifest.json")
    if not os.path.exists(idx_manifest):
        print("  索引缺失 → 自动重建中…")
        from scripts.index_builder import build_index
        t0 = time.time()
        info = build_index(ROOT)
        print(f"  重建完成（{(time.time()-t0)*1000:.0f}ms）：{info.get('case_count', 0)} case / {info.get('keyword_count', 0)} 关键词")
        check("索引重建成功", os.path.exists(idx_manifest))
    else:
        import json
        with open(idx_manifest, encoding="utf-8") as f:
            n = len(json.load(f))
        check("索引已存在且有效", n >= 10, f"{n} 条 manifest")

    print("\n[4/6] 检索冒烟测试（验证知识库真实可用）")
    try:
        t0 = time.time()
        hits = kp.search_case_hits("同事说我太敏感怎么回", top_k=3)
        ms = (time.time() - t0) * 1000
        check("检索可命中真实案例", len(hits) >= 1 and hits[0].total > 0,
              f"Top-1: {hits[0].case.case_id if hits else '无'}（{ms:.0f}ms）")
    except Exception as e:  # noqa: BLE001
        check("检索可命中真实案例", False, str(e))

    print("\n[5/6] 用户记忆初始化（多用户隔离）")
    from adapters.markdown_provider import MarkdownMemoryProvider
    mem = MarkdownMemoryProvider(ROOT, user_id="default")
    os.makedirs(mem.user_dir, exist_ok=True)
    probe = os.path.join(mem.user_dir, ".init_probe")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        check("用户记忆目录可写", True, "memory/users/default/")
    except OSError as e:
        check("用户记忆目录可写", False, str(e))

    print("\n[6/6] 默认能力加载验证")
    try:
        from scripts.preference import load_profile
        prof = load_profile()
        dims = (prof.get("dims") or {}).get("aggression", {})
        check("默认/个人画像可加载", bool(prof) and "value" in dims,
              f"aggression={dims.get('value')} confidence={dims.get('confidence')}")
    except Exception as e:  # noqa: BLE001
        check("默认/个人画像可加载", False, str(e))

    print("\n" + "=" * 52)
    if FAILS:
        print(f"  安装自检未通过：{len(FAILS)} 项失败")
        for f in FAILS:
            print(f"    ✗ {f}")
        print("  请按失败提示修复后重试，或到 GitHub Issues 反馈。")
        return 1
    print("  安装自检全部通过 ✅ 现在可以直接使用了。")
    print("  示例：python3 scripts/mouthpiece.py \"同事说我太敏感怎么回\"")
    print("=" * 52)
    return 0


if __name__ == "__main__":
    sys.exit(main())
