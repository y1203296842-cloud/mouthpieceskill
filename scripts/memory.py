"""scripts/memory.py — 记忆写入/学习 CLI（候选 → 置信度 → 稳定）。

用法：
  python3 scripts/memory.py remember "张三经常阴阳我，我不想撕破脸"     # 立即正式保存（用户说"记住这个"）
  python3 scripts/memory.py candidate "用户喜欢短回复" --cat style     # 写入候选记忆
  python3 scripts/memory.py promote "用户喜欢短回复" --to style        # 候选提升为稳定
  python3 scripts/memory.py feedback "太像AI了"                       # 记录反馈信号
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.markdown_provider import MarkdownMemoryProvider


def main() -> None:
    ap = argparse.ArgumentParser(description="记忆学习 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("remember", help="立即正式保存（用户明确要记住）")
    p1.add_argument("text")

    p2 = sub.add_parser("candidate", help="写入候选记忆")
    p2.add_argument("text")
    p2.add_argument("--cat", default="general", help="style|preference|taboo|people|event|general")

    p3 = sub.add_parser("promote", help="候选提升为稳定记忆")
    p3.add_argument("text")
    p3.add_argument("--to", default="style", help="style|taboos|preferences")

    p4 = sub.add_parser("feedback", help="记录反馈信号")
    p4.add_argument("text")

    args = ap.parse_args()
    mem = MarkdownMemoryProvider()

    if args.cmd == "remember":
        # 用户明确"记住这个" → 立即落盘为偏好（MVP 简化：写 preferences + style 观察）
        mem.set_preference(args.text)
        mem.promote(args.text, "style")
        print(f"已立即正式保存: {args.text}")
    elif args.cmd == "candidate":
        mem.add_candidate(args.text, args.cat)
        print(f"已写入候选记忆[{args.cat}]: {args.text}")
    elif args.cmd == "promote":
        mem.promote(args.text, args.to)
        print(f"已提升为稳定记忆[{args.to}]: {args.text}")
    elif args.cmd == "feedback":
        mem.add_feedback(args.text)
        print(f"已记录反馈: {args.text}")


if __name__ == "__main__":
    main()
