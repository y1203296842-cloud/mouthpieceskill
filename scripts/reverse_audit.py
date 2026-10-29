"""scripts/reverse_audit.py — 反向验收批量审计（计数：正确召回/错误召回/FP/fallback）。

运行：
  cd personal-mouthpiece-skill
  python3 scripts/reverse_audit.py

输出统计供最终报告使用：正确召回多少、错误召回多少、无匹配是否正确 fallback、是否硬套知识库。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.markdown_provider import MarkdownKnowledgeProvider
from scripts.mouthpiece import run


def audit() -> dict:
    kp = MarkdownKnowledgeProvider()
    all_replies = {rep for c in kp.list_cases() for rep in c.response}

    # (label, query, 期望)  期望: 'positive'(应高匹配直引) / 'reject'(应拒绝) / 'fallback'(无覆盖)
    batch = [
        # ---- 正向（应召回并直引）----
        ("正向-太敏感", "室友说我太敏感了怎么回", "positive"),
        ("正向-关系户", "帮我怼一下：你是不是关系户？", "positive"),
        ("正向-讨厌", "同事说讨厌我该怎么回", "positive"),
        ("正向-靠关系", "同事说你是关系户，怎么回", "positive"),
        ("正向-催婚", "过年亲戚一直问我对象怎么回", "positive"),
        # ---- 反向拒绝（关键词在但语义不符）----
        ("拒绝-自我改变", "我太敏感了，怎么让自己不那么敏感", "reject"),
        ("拒绝-同人物不同场景", "领导开会在会上批评我怎么办", "reject"),
        ("拒绝-追crush", "怎么追crush、怎么让她喜欢我", "reject"),
        ("拒绝-树≠身高", "我喜欢高的树，阳台能种吗", "reject"),
        ("拒绝-狗≠喂狗", "我家狗最近不吃饭怎么办", "reject"),
        ("拒绝-太抠≠太敏感", "同事说我太抠怎么回", "reject"),
        # ---- 无覆盖（fallback）----
        ("无覆盖-汇报", "明天要给领导汇报工作怎么准备", "fallback"),
        ("无覆盖-选车", "买第一辆车怎么选", "fallback"),
        ("无覆盖-安慰", "闺蜜失恋了我该怎么安慰她", "fallback"),
        ("无覆盖-下雨", "今天下雨没带伞怎么办", "fallback"),
    ]

    correct, wrong, fp, fallback, hard = 0, 0, 0, 0, 0
    detail = []
    for label, q, expect in batch:
        r = run(q)
        used_kb = r.direct_cited
        tier = r.tier
        # 硬套知识库：非直引路由仍返回了知识库原文
        hard_forcing = (r.route != "answer_direct") and any(x in all_replies for x in r.replies)
        if hard_forcing:
            hard += 1

        if expect == "positive":
            if used_kb:
                correct += 1
                res = "✓ 正确召回(直引)"
            else:
                wrong += 1
                res = f"✗ 漏召回 tier={tier} route={r.route}"
        elif expect == "reject":
            if used_kb:
                fp += 1
                wrong += 1
                res = f"✗ 错误召回(FP) tier={tier}"
            else:
                correct += 1
                res = f"✓ 正确拒绝 tier={tier} route={r.route}"
        else:  # fallback（无覆盖：low/none + pattern/style/招式 路由，且不返回知识库原文）
            if tier in ("low", "none") and not used_kb and r.route in ("pattern_generate", "style_generate", "move_generate"):
                fallback += 1
                correct += 1
                res = f"✓ 正确fallback route={r.route}（未硬套）"
            else:
                wrong += 1
                res = f"✗ 应为fallback，实际 tier={tier} route={r.route} used={used_kb}"
        detail.append(f"{res}  [{label}] {q}")

    return {
        "batch_size": len(batch),
        "correct": correct,
        "wrong": wrong,
        "false_positive": fp,
        "fallback": fallback,
        "hard_forcing": hard,
        "detail": detail,
    }


def main() -> None:
    s = audit()
    print("==== 反向验收批量审计 ====")
    print(f"样本数: {s['batch_size']} | 正确: {s['correct']} | 错误: {s['wrong']} "
          f"| 错误召回FP: {s['false_positive']} | fallback: {s['fallback']} "
          f"| 硬套知识库: {s['hard_forcing']}")
    print("---- 明细 ----")
    for line in s["detail"]:
        print(line)


if __name__ == "__main__":
    main()
