"""tests/test_mouthpiece.py — 私人嘴替测试（12 基础 + 6 项重构验收 A–F）。

运行：
  cd personal-mouthpiece-skill
  python3 -m tests.test_mouthpiece

覆盖：正常嘴替 / 怼人 / 知识直引 / 知识借鉴 / 知识注入 / 去重 / 用户偏好 /
人物记忆 / 事件记忆 / 跨 Provider / 跨 Host / 注入即检索
+ 重构验收：通用迁移 / 严格场景限制 / 具体匹配快速定位 / ASR修复 / trigger缺失恢复 / 失配标记 / 自动索引
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.base import (
    Case, CaseHit, GenerationPackage, QueryAnalysis, RetrievalService, RuntimeState,
    Strategy, StyleSpec, UserStyle, StyleProvider,
)
from adapters.fake_provider import build_fake_services, FakeRetrievalService
from adapters.host import host_manifest, validate_core_is_portable
from adapters.markdown_provider import (
    MarkdownKnowledgeProvider, MarkdownMemoryProvider, MarkdownRetrievalService,
    MarkdownRetriever, project_root,
)
from scripts.cleaner import build_case_from_text, correct_asr
from scripts.ingest import ingest_file
from scripts.mouthpiece import Mouthpiece, classify_mode, run

ROOT = project_root()


class ShortStyle(StyleProvider):
    def current_style(self) -> UserStyle:
        return UserStyle(tone="犀利", length="short", strength="medium", no_ai_tone=True)


class MediumStyle(StyleProvider):
    def current_style(self) -> UserStyle:
        return UserStyle(tone="犀利", length="medium", strength="medium", no_ai_tone=True)


class StyledService(MarkdownRetrievalService):
    """测试用：让嘴替核心使用指定风格（通过可注入的 RetrievalService，不改 Core）。"""
    def __init__(self, style: UserStyle) -> None:
        super().__init__()
        self._style = style

    def build_package(self, user_input: str, top_k: int = 6):
        pkg = super().build_package(user_input, top_k=top_k)
        pkg.style = self._style
        return pkg


class TestMouthpiece(unittest.TestCase):

    # ---------- 基础 12 项（适配新 schema） ----------
    def test_01_normal_mouthpiece(self):
        r = run("帮我回一下：你这项目怎么还没做完？")
        self.assertEqual(r.mode, "MOUTHPIECE")
        self.assertTrue(r.replies)
        self.assertTrue(all(len(x) < 80 for x in r.replies))

    def test_02_dui_ren(self):
        r = run("帮我怼一下：你是不是关系户？")
        self.assertEqual(r.mode, "MOUTHPIECE")
        self.assertTrue(r.direct_cited)
        self.assertTrue(any("我还能靠关系让你出去" in x for x in r.replies))

    def test_03_direct_cite(self):
        r = run("同事说你是关系户，怎么回")
        kp = MarkdownKnowledgeProvider()
        case = kp.get_case("guanxi-靠关系")
        self.assertTrue(r.direct_cited)
        self.assertIsNotNone(case)
        self.assertIn(r.replies[0], case.response)

    def test_04_strategy_adapt(self):
        r = run("同事说你怎么每次都这么闲")
        self.assertEqual(r.mode, "MOUTHPIECE")
        self.assertFalse(r.direct_cited)
        self.assertTrue(r.strategy_used)
        kp = MarkdownKnowledgeProvider()
        all_replies = [rep for c in kp.list_cases() for rep in c.response]
        self.assertNotIn(r.replies[0], all_replies)

    def test_05_knowledge_ingestion_and_classify(self):
        self.assertEqual(classify_mode("这是我新采集的一批怼人文案，加入知识库"), "KNOWLEDGE_INGESTION")
        self.assertEqual(classify_mode("记住这个"), "MEMORY_WRITE")
        self.assertEqual(classify_mode("太像AI了"), "FEEDBACK")
        raw = ("据说你是关系户要怎么创飞他 打狗型我还能靠关系让你出去哈哈哈 "
               "差异型你不是吗那你完了哈哈哈 复读型不知道啊我发誓我考进来的 "
               "威胁型你领导是谁哈哈哈 裁员型别老研究我怎么进来抓紧研究自己怎么留下来吧")
        res = build_case_from_text(raw)
        self.assertTrue(res.is_knowledge)
        self.assertGreaterEqual(res.replies_extracted, 4)
        self.assertGreater(res.noise_removed, 0)
        self.assertIn("关系户", res.cleaned.canonical_trigger)
        self.assertFalse(any("哈哈哈" in rep for rep in res.cleaned.response))

    def test_06_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            kp = MarkdownKnowledgeProvider(root=tmp)
            md = os.path.join(tmp, "creator.md")
            with open(md, "w", encoding="utf-8") as f:
                f.write(
                    "### 视频 001\n\n"
                    "* 视频标题：同事说你是关系户怎么回\n"
                    "* 视频链接：https://x/1\n"
                    "* 内容方向：职场\n"
                    "* 结构标记：打狗、差异\n"
                    "* 点赞：1万\n"
                    "#### 视频逐字稿\n\n"
                    "> 同事说你是关系户怎么回打狗型我还能靠关系让你出去哈哈哈差异型你不是吗哈哈哈\n"
                )
            first = ingest_file(md, knowledge=kp)
            second = ingest_file(md, knowledge=kp)
            self.assertGreaterEqual(first.new_cases, 1)
            self.assertEqual(second.new_cases, 0)
            self.assertGreaterEqual(second.duplicates, 1)

    def test_07_user_preference_length(self):
        short = Mouthpiece(service=StyledService(ShortStyle().current_style())).respond("帮我回：同事说你是关系户")
        medium = Mouthpiece(service=StyledService(MediumStyle().current_style())).respond("帮我回：同事说你是关系户")
        self.assertTrue(short.direct_cited)
        self.assertLess(len(short.replies), len(medium.replies))
        self.assertLessEqual(len(short.replies), 2)

    def test_08_people_memory(self):
        retriever = MarkdownRetriever()
        b = retriever.retrieve_user("张三又说这个了")
        self.assertIn("张三", b.people)
        r = run("张三又说这个了")
        self.assertIn("张三", r.context_used["people"])

    def test_09_event_memory(self):
        retriever = MarkdownRetriever()
        b = retriever.retrieve_user("张三甩工作的事")
        self.assertTrue(any("张三甩工作" in e for e in b.events))

    def test_10_cross_provider(self):
        services = build_fake_services()
        mp = Mouthpiece(service=services["service"])
        r = mp.respond("帮我怼：你是关系户")
        self.assertTrue(r.direct_cited)
        self.assertIn("我还能靠关系让你出去", r.replies)

    def test_11_cross_host(self):
        problems = validate_core_is_portable(ROOT)
        self.assertEqual(problems, [])
        for host in ("claude_code", "trae", "workbuddy"):
            m = host_manifest(host)
            self.assertIn("SKILL.md", m["requires"])
        for sub in ("knowledge", "memory"):
            self.assertTrue(os.path.isdir(os.path.join(ROOT, sub)))

    def test_12_ingestion_immediately_retrievable(self):
        with tempfile.TemporaryDirectory() as tmp:
            kp = MarkdownKnowledgeProvider(root=tmp)
            md = os.path.join(tmp, "new.md")
            with open(md, "w", encoding="utf-8") as f:
                f.write(
                    "### 视频 001\n\n"
                    "* 视频标题：同事说你带薪拉屎怎么办\n"
                    "* 视频链接：https://x/2\n"
                    "* 内容方向：职场\n"
                    "* 结构标记：高冷\n"
                    "* 点赞：2万\n"
                    "#### 视频逐字稿\n\n"
                    "> 同事说你带薪拉屎怎么办高冷型个人私事不劳你操心哈哈哈\n"
                )
            rep = ingest_file(md, knowledge=kp)
            self.assertGreaterEqual(rep.new_cases, 1)
            hits = kp.search_cases("同事说我带薪拉屎")
            self.assertTrue(any("带薪" in h.canonical_trigger or any("带薪" in k for k in h.keywords)
                                for h in hits))

    # ---------- 重构验收 A–F ----------
    def test_13_accept_A_broad_migration(self):
        """验收A：用户'室友说我太敏感了怎么回' → 召回原本来自'同事说你太敏感'的通用回答。"""
        kp = MarkdownKnowledgeProvider()
        hits = kp.search_case_hits("室友说我太敏感了怎么回", top_k=5)
        # broad 的 太敏感 案例必须在靠前，且不被"室友≠同事"的关系/场景惩罚挡掉
        self.assertTrue(hits, "应当召回候选")
        top_ids = [h.case.case_id for h in hits]
        self.assertIn("taimingan-太敏感", top_ids[:2])
        # 直接生成也应命中
        r = run("室友说我太敏感了怎么回")
        self.assertTrue(r.direct_cited)

    def test_14_accept_B_quick_locate(self):
        """验收B：'同事说讨厌我该怎么回' → 快速定位到'别人表达讨厌/排斥'的案例，而非扫全库。"""
        kp = MarkdownKnowledgeProvider()
        hits = kp.search_case_hits("同事说讨厌我该怎么回", top_k=3)
        self.assertTrue(hits)
        self.assertEqual(hits[0].case.case_id, "reject-tuoyanni")
        r = run("同事说讨厌我该怎么回")
        self.assertTrue(r.direct_cited)

    def test_15_accept_C_asr_fix(self):
        """验收C：ASR 纠错 + trigger 缺失恢复（标题比逐字稿准）。"""
        self.assertEqual(correct_asr("你太敏gan了"), "你太敏感了")
        # 逐字稿缺关键信息，但标题含完整场景 → canonical_trigger 应补全
        res = build_case_from_text(
            "打狗型我还能靠关系让你出去哈哈哈",
            title="同事说你靠关系怎么回",
            labels=["打狗"],
        )
        self.assertTrue(res.is_knowledge)
        self.assertIn("靠关系", res.cleaned.canonical_trigger)
        self.assertEqual(res.cleaned.status, "verified")

    def test_16_accept_D_mismatch_marked(self):
        """验收D：trigger↔response 明显失配且无标题/上下文可恢复 → 标记 needs_review，不入正式库。"""
        res = build_case_from_text("亲戚过年关心你 打狗型有一些哈哈哈")
        self.assertTrue(res.is_knowledge)
        self.assertEqual(res.cleaned.status, "needs_review")
        self.assertTrue(res.cleaned.needs_review_reason)

    def test_17_accept_E_intent_scene_not_pure_keyword(self):
        """验收E：检索依赖 语义+意图+场景适配，而非单纯关键词命中。"""
        kp = MarkdownKnowledgeProvider()
        # (1) '玻璃心'是'太敏感'的变体关键词 → 靠意图/语义召回，不依赖原文精确词
        hits = kp.search_case_hits("有人说我玻璃心怎么办", top_k=5)
        self.assertTrue(any(h.case.case_id == "taimingan-太敏感" for h in hits))
        # (2) 场景适配：'室友说我卡点下班'虽命中'卡点/下班'关键词，但因领域冲突（职场限定 vs 室友）触发 scope 惩罚
        hits2 = kp.search_case_hits("室友说我卡点下班怎么回", top_k=3)
        self.assertTrue(hits2)
        kadian = next((h for h in hits2 if h.case.case_id == "kadian-卡点下班"), None)
        self.assertIsNotNone(kadian, "职场限定案例应被召回但被惩罚")
        self.assertGreater(kadian.scope_penalty, 0, "领域冲突应产生 scope 惩罚")

    def test_18_accept_F_new_case_auto_index(self):
        """验收F：新增 case 自动进入 清洗→标准化→索引 流程（不要求人工维护索引）。"""
        with tempfile.TemporaryDirectory() as tmp:
            kp = MarkdownKnowledgeProvider(root=tmp)
            md = os.path.join(tmp, "new.md")
            with open(md, "w", encoding="utf-8") as f:
                f.write(
                    "### 视频 001\n\n"
                    "* 视频标题：对象说你总不接电话怎么哄\n"
                    "* 视频链接：https://x/3\n"
                    "* 内容方向：情感\n"
                    "* 结构标记：撩拨\n"
                    "* 点赞：2万\n"
                    "#### 视频逐字稿\n\n"
                    "> 对象说你总不接电话怎么办撩拨型你是不是想我想得睡不着哈哈哈\n"
                )
            rep = ingest_file(md, knowledge=kp)
            self.assertGreaterEqual(rep.new_cases, 1)
            # 索引被自动重建，且包含新案例
            mp_path = os.path.join(tmp, "knowledge", "index", "case_manifest.json")
            self.assertTrue(os.path.exists(mp_path))
            with open(mp_path, encoding="utf-8") as f:
                manifest = json.load(f)
            new_ids = [e["case_id"] for e in manifest]
            self.assertTrue(any(cid for cid in new_ids if "不接电话" in cid or "对象" in cid))

    def test_19_batch_cross_relation_recall(self):
        """通用性全库重判验收：每一个 broad 案例，换关系提问都必须召回（不只'太敏感'）。"""
        kp = MarkdownKnowledgeProvider()
        broad = [c for c in kp.list_cases() if c.scope == "broad"]
        self.assertGreaterEqual(len(broad), 8, "broad 案例应有足够数量（全库逐案重判）")
        missed = []
        for c in broad:
            # 挑一个与原 trigger 中角色不同的跨关系问法（室友/朋友/家人/别人/对象）
            q = None
            for ex in c.query_examples:
                if any(r in ex for r in ("室友", "朋友", "家人", "别人", "对象")):
                    q = ex
                    break
            if q is None:
                q = f"{c.canonical_trigger}怎么回"
            hits = kp.search_case_hits(q, top_k=5)
            found = any(h.case.case_id == c.case_id for h in hits)
            # 跨关系问法不应被错误领域惩罚（broad 案例 scope_penalty 应为 0）
            if found:
                hit = next(h for h in hits if h.case.case_id == c.case_id)
                self.assertEqual(hit.scope_penalty, 0.0,
                                 f"{c.case_id} 是 broad 但被 {q} 触发领域惩罚（不应发生）")
            else:
                missed.append((c.case_id, q))
        self.assertEqual(missed, [], f"以下 broad 案例换关系提问未召回: {missed}")

    def test_20_accept_D_E_generality_both_directions(self):
        """验收D/E：response 比 trigger 更通用 → 换关系可召回；带领域/关系限制的 → 不错误泛化。"""
        kp = MarkdownKnowledgeProvider()
        # D-1: 学历案例 trigger 已泛化（拿学历/读书说事）→ '亲戚说我读书没用' 也能召回
        hits = kp.search_case_hits("亲戚说我读书没用怎么怼", top_k=5)
        self.assertTrue(any(h.case.case_id == "daxuesheng-亏你还是大学生" for h in hits),
                        "泛化后的学历案例应被亲戚问法召回（response 比原 trigger 更通用）")
        # D-2: 使唤案例已泛化（守边界）→ '家里总让我跑腿' 也能召回
        hits2 = kp.search_case_hits("家里总让我跑腿怎么办", top_k=5)
        self.assertTrue(any(h.case.case_id == "tuodi-使唤你干活" for h in hits2),
                        "泛化后的守边界案例应被家人问法召回")
        # E: 职场限定的卡点下班，'室友'语境 → 领域冲突被惩罚
        hits3 = kp.search_case_hits("室友说我卡点下班怎么回", top_k=3)
        self.assertTrue(hits3)
        kadian = next((h for h in hits3 if h.case.case_id == "kadian-卡点下班"), None)
        self.assertIsNotNone(kadian, "职场限定案例应被召回但被惩罚")
        self.assertGreater(kadian.scope_penalty, 0, "领域冲突应产生 scope 惩罚")
        # E-2: 权力关系（领导不喜欢）不能被错误泛化成 broad → 仍为 relational 且带关系限制
        bxh = kp.get_case("buxihuanni-领导不喜欢")
        self.assertEqual(bxh.scope, "relational", "权力/上下级关系案例不应被泛化成 broad")
        self.assertIn("领导", bxh.relation_constraints, "领导案例应保留关系限制")

    def test_21_auto_pipeline_generalizes_new_case(self):
        """新增知识自动执行通用性判断：broad 案例的 trigger 自动泛化 + 生成跨关系问法。"""
        from scripts.cleaner import build_case_from_text, generalize_trigger
        # 原始 trigger 是'同事说你太敏感'（窄），broad 判定后应自动泛化为'别人…'并给跨关系问法
        res = build_case_from_text(
            "打狗型不敏感还不知道你这么坏哈哈哈", title="同事说你太敏感怎么回", labels=["打狗"])
        self.assertTrue(res.is_knowledge)
        self.assertEqual(res.cleaned.scope, "broad")
        self.assertIn("别人", res.cleaned.canonical_trigger,
                      "broad 案例的 canonical_trigger 应自动泛化角色，而非锁死'同事'")
        self.assertTrue(any("室友" in q for q in res.cleaned.query_examples),
                        "broad 案例应自动生成跨关系问法（室友/朋友/家人）")
        # 泛化函数直接验证：领导（权力关系）→ 上级（保留权力语义，不collapse成别人）
        self.assertEqual(generalize_trigger("领导说你太敏感", "broad"), "上级说你太敏感")

    # ---------- 反向验收（拒绝错误召回 / 无覆盖 fallback / 不硬套知识库） ----------
    def _assert_not_kb_reply(self, r):
        kp = MarkdownKnowledgeProvider()
        all_replies = {rep for c in kp.list_cases() for rep in c.response}
        for x in r.replies:
            self.assertNotIn(x, all_replies, "低匹配不应硬套知识库原文")

    def test_22_reverse_reject_false_positives(self):
        """反向验收1：看起来有关键词、但语义/意图不符 → 必须拒绝（不直引）。"""
        negatives = [
            ("同关键词不同意图", "我太敏感了，怎么让自己不那么敏感", False),
            ("同人物不同场景", "领导开会在会上批评我怎么办", False),
            ("同场景不同意图", "怎么追crush、怎么让她喜欢我", False),
            ("表面词相似语义不符", "我喜欢高的树，阳台能种吗", False),
            ("表面词相似语义不符", "我家狗最近不吃饭怎么办", False),
            ("表面词相似语义不符(太抠≠太敏感)", "同事说我太抠怎么回", False),
        ]
        for label, q, _ in negatives:
            r = run(q)
            self.assertFalse(r.direct_cited, f"{label}: [{q}] 不应直引知识库")
            if r.tier == "low":
                self._assert_not_kb_reply(r)

    def test_23_reverse_no_coverage_fallback(self):
        """反向验收2：知识库完全没有覆盖 → 不硬套最相似 case → 正常 fallback。"""
        no_cover = [
            "明天要给领导汇报工作怎么准备",
            "买第一辆车怎么选",
            "闺蜜失恋了我该怎么安慰她",
            "今天下雨没带伞怎么办",
        ]
        for q in no_cover:
            r = run(q)
            self.assertIn(r.tier, ("low", "none"), f"[{q}] 应判为低/无匹配")
            self.assertFalse(r.direct_cited, f"[{q}] 不应直引")
            self._assert_not_kb_reply(r)
            self.assertIn("fallback", r.note, f"[{q}] 应走 fallback 路径")

    def test_24_reverse_broad_power_conflict_medium(self):
        """反向验收3：broad 案例被权力关系指控 → 降级 medium 改写，不硬直引狠话。"""
        r = run("老板说我玻璃心，我要忍着吗")
        self.assertEqual(r.tier, "medium")
        self.assertFalse(r.direct_cited)
        self.assertTrue(r.strategy_used)

    # ---------- 系统性重构验收（KB 强制调用 / 不暴露 / 多答案自选 / 两层检索 / 无AI劝架 / 性能 / 多平台） ----------
    def _leak_tokens(self) -> tuple:
        return ("case_id", "scope", "版本", "文件夹", "来源", "博主", "匹配分", "候选",
                "检索", "provider", "manifest", "knowledge/index", "cases/")

    def test_25_kb_forced_call(self):
        """知识库存在时，嘴替问题必须触发检索（gatekeeper）。"""
        for q in ("室友说我太敏感了怎么回", "帮我怼一下：你是不是关系户？", "同事说我太抠怎么回"):
            r = run(q)
            self.assertTrue(r.state.KB_AVAILABLE, f"[{q}] 知识库应可用")
            self.assertTrue(r.state.KB_CALLED, f"[{q}] KB 必须被调用")

    def test_26_kb_never_exposes_internals(self):
        """知识库内部结构绝不进入用户可见回复，也不询问用户选择。"""
        for q in ("室友说我太敏感了怎么回", "同事说讨厌我该怎么回", "同事说我太抠怎么回",
                  "明天要给领导汇报工作怎么准备", "帮我怼一下：你是不是关系户？"):
            r = run(q)
            joined = " ".join(r.replies)
            for tok in self._leak_tokens():
                self.assertNotIn(tok, joined, f"[{q}] 回复泄漏内部字段: {tok}")
            self.assertNotIn("你要哪个", joined)
            self.assertNotIn("版本", joined)

    def test_27_multi_answer_auto_select(self):
        """同一问题多个高匹配候选 → 自动选一个，绝不让用户选版本/答案。"""
        class MultiService(RetrievalService):
            def available(self): return True
            def build_package(self, u, top_k=6):
                c1 = Case(case_id="a-1", canonical_trigger="别人说你太敏感", intent="回应",
                          response=["版本A第一句"], scope="broad", keywords=["敏感"], status="verified")
                c2 = Case(case_id="a-2", canonical_trigger="别人说你太敏感", intent="回应",
                          response=["版本B第一句"], scope="broad", keywords=["敏感"], status="verified")
                h1 = CaseHit(case=c1, semantic=10.0, total=11.0, relation_fit=1.0)
                h2 = CaseHit(case=c2, semantic=9.0, total=10.0, relation_fit=1.0)
                return GenerationPackage(query=u, analysis=QueryAnalysis(is_accusation=True),
                                         style_spec=StyleSpec(), patterns=[],
                                         answer_candidates=[h1, h2],
                                         state=RuntimeState(KB_AVAILABLE=True, KB_CALLED=True))
            def search(self, q, top_k=6): return []
            def get_patterns(self, q, top_k=4): return []
        r = Mouthpiece(service=MultiService()).respond("同事说我太敏感了怎么回")
        self.assertEqual(r.route, "answer_direct")
        self.assertTrue(r.best_case_id in ("a-1", "a-2"))
        self.assertTrue(r.replies)
        self.assertTrue(any("版本A" in x or "版本B" in x for x in r.replies))

    def test_28_no_answer_but_pattern_generates(self):
        """无 exact answer 但存在回应思路 → 用 pattern 生成，而非完全自由发挥。"""
        r = run("别人说我太高冷怎么回")
        self.assertEqual(r.route, "move_generate")
        self.assertFalse(r.direct_cited)
        self.assertTrue(r.replies)
        self.assertIn("fallback", r.note)
        self._assert_not_kb_reply(r)

    def test_29_no_coverage_style_generates(self):
        """完全无覆盖 → 用全局风格规范生成，且不出现 AI 劝架腔。"""
        for q in ("我想学做番茄炒蛋怎么回", "狗狗生病了该怎么办", "怎么选股票开户"):
            r = run(q)
            self.assertEqual(r.route, "move_generate")
            self.assertTrue(r.replies)
            for x in r.replies:
                self.assertNotIn("我理解你的感受", x)
                self.assertNotIn("你可以尝试", x)
                self.assertNotIn("心平气和地沟通", x)
                self.assertNotIn("建议你", x)
            self._assert_not_kb_reply(r)

    def test_30_style_regression_preserves_kb_voice(self):
        """风格回归：直引时保留原案例的攻击性/短句，不弱化、不 AI 劝架。"""
        for q in ("室友说我太敏感了怎么回", "同事说讨厌我该怎么回", "帮我怼一下：你是不是关系户？"):
            r = run(q)
            self.assertTrue(r.direct_cited, f"[{q}] 应直引高质量案例")
            self.assertTrue(r.replies)
            for x in r.replies:
                self.assertLessEqual(len(x), 30, f"[{q}] 直引应保持短句")
                self.assertNotIn("我理解你的感受", x)
                self.assertNotIn("心平气和", x)

    def test_31_performance_no_minutes(self):
        """性能：≥20 个真实问题，检索不出现分钟级延迟。"""
        import statistics as st
        import time
        svc = MarkdownRetrievalService()
        queries = [
            "室友说我太敏感了怎么回", "同事说讨厌我该怎么回", "帮我怼一下：你是不是关系户？",
            "过年亲戚一直问我对象怎么回", "同事说我太抠怎么回", "老板说我玻璃心我要忍着吗",
            "朋友说我太小气了怎么回", "部门里有人排挤我怎么办", "对象说我太粘人了怎么办",
            "明天要给领导汇报工作怎么准备", "买第一辆车怎么选", "闺蜜失恋了我该怎么安慰她",
            "今天下雨没带伞怎么办", "领导不喜欢我怎么办", "男朋友老说好累我该怎么接",
            "对象吃醋了我怎么哄", "同事卡点下班被说摸鱼怎么回", "亲戚催婚我要怼回去吗",
            "怎么追crush让她喜欢我", "陌生人让座我不乐意怎么拒", "别人说我太高冷怎么回",
            "我该怎么说才能让领导觉得我靠谱",
        ]
        kb_ms, total_ms = [], []
        for q in queries:
            pkg = svc.build_package(q)
            kb_ms.append(pkg.state.KB_SEARCH_MS)
            total_ms.append(pkg.state.TOTAL_MS)
        self.assertLess(st.mean(kb_ms), 2000, f"KB 检索平均需 <2s，实际 {st.mean(kb_ms):.0f}ms")
        self.assertLess(max(kb_ms), 5000, "KB 检索 P99 需 <5s")
        self.assertLess(st.mean(total_ms), 15000, f"端到端打包平均需 <15s，实际 {st.mean(total_ms):.0f}ms")
        kb_sorted = sorted(kb_ms)
        p95 = kb_sorted[int(len(kb_sorted) * 0.95)]
        print(f"\n[perf] n={len(queries)} kb_avg={st.mean(kb_ms):.0f}ms kb_p95={p95:.0f}ms "
              f"kb_max={max(kb_ms):.0f}ms total_avg={st.mean(total_ms):.0f}ms")

    def test_32_multi_provider_and_host_kb_entry(self):
        """跨 Provider 都走 RetrievalService；各宿主 Adapter 都有可执行的 KB 调用入口。"""
        from adapters.host import claude_code, trae, workbuddy
        # Markdown + Fake 两种 RetrievalService 均可构建 GenerationPackage 并执行强制检索
        for svc in (MarkdownRetrievalService(), FakeRetrievalService()):
            pkg = svc.build_package("同事说我是关系户怎么回")
            self.assertEqual(pkg.state.KB_AVAILABLE, pkg.state.KB_CALLED)
            self.assertIsNotNone(pkg.style_spec)
        # 每个宿主都写明 KB 调用入口（retrieve.py --json）
        for mod in (claude_code, trae, workbuddy):
            self.assertIn("retrieve.py", mod.pack_instructions(), f"{mod.__name__} 缺 KB 调用入口")
            self.assertEqual(mod.validate(ROOT), [], f"{mod.__name__} validate 应通过")


# ---- v1.1.0+ 类别级测试：称呼 / 情绪价值 / 内部隔离 / 数据清洗拆分 / 自然口语 ----

_LEAK_TOKENS = ["case_id", "知识库", "fallback", "版本", "文件夹", "来源", "检索", "score",
                "候选", "provider", "KB", "匹配分", "index", "case", "classify_mode",
                "策略id", "best_case", "命中"]


def _no_leak(uv) -> bool:
    blob = (uv.lead or "") + (uv.suggestion or "") + "".join(uv.replies)
    return not any(t.lower() in blob.lower() for t in _LEAK_TOKENS)


class TestV11Plus(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeRetrievalService()
        self.fake.set_name("")

    # ---- 用户交互类 ----
    def test_33_name_first_contact_asks_once(self):
        """首次接触且未记住称呼 → 自然询问一次（greeting_needs_name）。"""
        r = Mouthpiece(self.fake).respond("室友说我太敏感了怎么回")
        uv = r.user_view()
        self.assertTrue(uv.greeting_needs_name)
        self.assertIn("称呼", uv.lead)
        self.assertTrue(_no_leak(uv))

    def test_34_name_write_persists(self):
        """'叫我小明吧' → 保存称呼并确认；不展示内部实现。"""
        m = Mouthpiece(self.fake)
        r = m.respond("叫我小明吧")
        self.assertEqual(r.mode, "NAME_WRITE")
        self.assertEqual(m.service.get_name(), "小明")
        uv = r.user_view()
        self.assertIn("小明", uv.lead)
        self.assertNotIn("吧", uv.lead)   # 语气词已剔除
        self.assertTrue(_no_leak(uv))

    def test_35_name_used_once_not_every_sentence(self):
        """记住称呼后自然使用：称呼只在开场，每条话术里绝不放称呼，无'小明，我建议'模板。"""
        self.fake.set_name("小明")
        uv = Mouthpiece(self.fake).respond("同事说讨厌我该怎么回").user_view()
        for rep in uv.replies:
            self.assertNotIn("小明", rep)
        self.assertNotIn("小明，我建议", uv.lead)

    # ---- 场景判断 / 情绪价值类（动态，不模板化）----
    def test_36_scenario_sense_is_dynamic_not_templated(self):
        """不同场景 lead 各不相同：权力/多人/亲密/厌恶，互不相同且各自贴切。"""
        self.fake.set_name("小明")
        power = Mouthpiece(self.fake).respond("老板说我玻璃心我要忍着吗").user_view().lead
        multi = Mouthpiece(self.fake).respond("同事在饭局上说我学历低怎么回").user_view().lead
        self.assertIn("上位", power)
        self.assertIn("人多", multi)
        self.assertNotEqual(power, multi)
        # 记忆厌恶只影响建议方向（别失控/稳住），不变成永远往狠
        dislike = Mouthpiece(self.fake).respond("我本来就烦死这个人了还天天阴阳我，怎么回").user_view().lead
        self.assertTrue(("别失控" in dislike) or ("稳住" in dislike))

    def test_37_mouthpiece_always_has_lead_and_short_replies(self):
        """MOUTHPIECE 回答前都有轻量场景判断 lead；话术保持短句。"""
        self.fake.set_name("小明")
        uv = Mouthpiece(self.fake).respond("同事说讨厌我该怎么回").user_view()
        self.assertTrue(uv.lead)
        for rep in uv.replies:
            self.assertLessEqual(len(rep), 40)

    # ---- 内部隔离类 ----
    def test_38_internal_state_never_leaks(self):
        """多查询（含无覆盖 fallback）下，用户可见内容绝不含任何内部实现词。"""
        self.fake.set_name("小明")
        m = Mouthpiece(self.fake)
        queries = [
            "同事说讨厌我该怎么回",
            "老板说我玻璃心我要忍着吗",
            "别人说你太敏感了怎么怼",
            "我想学做番茄炒蛋怎么回",       # 无覆盖 → fallback
            "狗狗生病了该怎么办",
        ]
        for q in queries:
            r = m.respond(q)
            uv = r.user_view()
            self.assertTrue(uv.replies or uv.lead, f"空回答：{q}")
            self.assertTrue(_no_leak(uv), f"内部信息泄漏：{q}")
            # 绝不把 mode/route/note 等放进用户回答
            self.assertNotIn(r.mode, uv.lead + "".join(uv.replies))

    def test_39_no_coverage_fallback_is_hidden(self):
        """无覆盖 → fallback 但对用户不可见：只看到自然回答，看不到'知识库/fallback/没有'。"""
        self.fake.set_name("小明")
        r = Mouthpiece(self.fake).respond("我想学做番茄炒蛋怎么回")
        uv = r.user_view()
        self.assertEqual(r.route, "move_generate")
        self.assertTrue(uv.replies)
        self.assertTrue(_no_leak(uv))

    def test_40_never_asks_user_to_choose_kb(self):
        """内部候选再多也不询问用户选版本/case/文件夹。"""
        self.fake.set_name("小明")
        uv = Mouthpiece(self.fake).respond("同事说讨厌我该怎么回").user_view()
        blob = uv.lead + uv.suggestion + "".join(uv.replies)
        for bad in ("你想用哪个", "选哪个", "有两个版本", "选择case", "选文件夹", "要哪种"):
            self.assertNotIn(bad, blob)

    # ---- 数据清洗类（一个视频=多个 case / 玩笑拆分）----
    def test_41_transcript_splits_embedded_dialogues(self):
        """逐字稿里一个连续回答含多个独立场景 → 按语义拆成多个 [trigger→reply]。"""
        from scripts.ingest_transcript import split_exchanges
        pairs = split_exchanges("你爸妈不要你喽，你爸妈来接你喽。你什么意思，没什么意思，逗你玩啊，怎么玩笑开到自己身上就不好笑了。")
        trigs = [t for t, _ in pairs]
        self.assertGreaterEqual(len(pairs), 2)
        self.assertTrue(any("不要你喽" in t for t in trigs), "应拆出'你爸妈不要你喽'")
        self.assertTrue(any(("较真" in t) or ("开玩笑" in t) for t in trigs), "应拆出'说你太较真/只是开玩笑'")

    def test_42_joke_two_cases_separate_and_retrievable(self):
        """玩笑两个 case 必须独立文件、独立检索、不混。"""
        kb = MarkdownKnowledgeProvider()
        ids = [c.case_id for c in kb.list_cases()]
        self.assertIn("joke-你爸妈不要你喽", ids)
        self.assertIn("joke-只是开玩笑", ids)
        m = Mouthpiece()
        r1 = m.respond("别人说你爸妈不要你喽怎么回")
        self.assertEqual(r1.best_case_id, "joke-你爸妈不要你喽")
        self.assertIn("你爸妈接你来喽", r1.replies[0])
        r2 = m.respond("朋友说我太较真、只是开个玩笑怎么回")
        self.assertEqual(r2.best_case_id, "joke-只是开玩笑")
        self.assertIn("玩笑开到自己身上", r2.replies[0])
        self.assertNotIn("你爸妈接你来喽", "".join(r2.replies), "两 case 不能混")

    # ---- 自然用户口语测试（真实集成）----
    def test_43_natural_real_world_queries(self):
        """模拟真实用户说话方式，走真实知识库；只验证用户可见回答自然、无内部泄漏。"""
        m = Mouthpiece()
        natural = [
            "我室友又说我太敏感了，怎么怼",
            "那个傻逼同事又拿我学历说事",
            "朋友阴阳我没对象咋回",
            "别人说我玻璃心，我想阴阳回去",
            "有人在人多的地方这样说我怎么办",
            "同事总打听我工资怎么回",
            "crush打游戏太菜了怎么撩",
        ]
        for q in natural:
            r = m.respond(q)
            uv = r.user_view()
            self.assertTrue(uv.replies, f"自然查询无回答：{q}")
            self.assertTrue(_no_leak(uv), f"自然查询泄漏内部信息：{q}")


# ---- 能力路由（多能力分离，绝不串台）+ 招式驱动生成 ----
class TestCapabilityAndMoves(unittest.TestCase):
    def test_44_capability_separation_no_cross(self):
        """祝福请求绝不给回怼；回怼请求绝不给祝福；高情商独立。"""
        b = Mouthpiece().respond("给我写个新年祝福")
        self.assertEqual(b.mode, "BLESSING")
        self.assertTrue(any("祝福" in x or "愿" in x or "新年" in x for x in b.replies))
        for x in b.replies:
            self.assertNotIn("关系户", x)
        r = Mouthpiece().respond("同事说我靠关系怎么回")
        self.assertEqual(r.mode, "MOUTHPIECE")
        for x in r.replies:
            self.assertNotIn("新年快乐", x)
        h = Mouthpiece().respond("高情商回复老板催进度")
        self.assertEqual(h.mode, "HIGH_EQ")
        self.assertTrue(h.replies)

    def test_45_blessing_never_touches_retort_kb(self):
        """祝福走独立能力入口，不查回怼知识库、不回怼资料。"""
        b = Mouthpiece().respond("帮我写个婚礼祝福语")
        self.assertEqual(b.mode, "BLESSING")
        self.assertTrue(any("喜" in x or "白头" in x or "恭喜" in x for x in b.replies))

    def test_46_plan_conflict_to_moves(self):
        """未知回怼场景 → plan 给出冲突类型 + 推荐招式（含骨架）。"""
        from scripts.plan import plan
        p = plan("同事说你怎么这么笨怎么回")
        self.assertEqual(p["capability"], "RETORT")
        self.assertIn("被质疑", p["conflict_type"])
        mids = [m["id"] for m in p["recommended_moves"]]
        self.assertTrue(any(m in mids for m in ("m1", "m2", "m3")), f"应含不接/反问/扣回，实际 {mids}")
        self.assertTrue(any(m.get("skeleton") for m in p["recommended_moves"]), "招式应带生成骨架")
        pl = plan("领导说这个你自己想办法怎么办")
        self.assertEqual(pl["relation"], "领导")
        self.assertLessEqual(pl["strength_cap"], 4)

    def test_47_move_library_is_finite_and_complete(self):
        """招式库存在且每个招式都有结构骨架；9 招式 + 2 目标模式。"""
        import glob
        move_files = sorted(glob.glob(os.path.join(ROOT, "knowledge", "moves", "*.md")))
        self.assertGreaterEqual(len(move_files), 9)
        missing = [f for f in move_files if "## 核心结构" not in open(f, encoding="utf-8").read()]
        self.assertEqual(missing, [], f"以下招式缺生成骨架：{missing}")
        self.assertTrue(any("g2" in f for f in move_files), "应有撩·暧昧推拉目标模式")

    def test_48_retort_fallback_has_move_method(self):
        """回怼无覆盖时仍可用（plan 给方法，用户可见回答自然、不泄漏内部）。"""
        from scripts.plan import plan
        q = "同事说我头发少怎么回"
        p = plan(q)
        self.assertTrue(p["recommended_moves"], "应给出招式")
        r = Mouthpiece().respond(q)
        uv = r.user_view()
        self.assertTrue(uv.replies, "应仍有可用回答")


# ---- 招式驱动生成（凌晨方案核心落地）----
class TestMoveDrivenGeneration(unittest.TestCase):
    def test_49_unseen_scenario_move_driven_not_plain_ai(self):
        """知识库没覆盖的新回怼场景 → move_generate，招式随场景变化、无AI劝架腔。"""
        r1 = Mouthpiece().respond("同事说我头发少怎么回")   # 质疑外貌 → 反问/不接
        r2 = Mouthpiece().respond("领导说这个你自己想办法怎么办")  # 甩锅给领导 → 捧杀/装傻
        self.assertEqual(r1.route, "move_generate")
        self.assertEqual(r2.route, "move_generate")
        self.assertNotEqual(r1.replies, r2.replies, "不同冲突应命中不同招式，输出不能雷同")
        for line in r1.replies + r2.replies:
            self.assertFalse(any(w in line for w in
                ("我理解", "你可以尝试", "从沟通角度", "建议你", "心平气和", "每个人都有自己的看法")),
                f"出现AI劝架腔：{line}")

    def test_50_no_false_positive_on_broad_trigger(self):
        """红线的'对不上号'回归：泛化'阴阳'query 不得直引'喂狗'案例。"""
        r = Mouthpiece().respond("朋友阴阳我没对象咋回")
        joined = "".join(r.replies)
        self.assertNotIn("喂狗", joined)
        self.assertNotIn("狗叫", joined)
        self.assertNotEqual(r.route, "answer_direct", "不该直引无关案例")


# ---- 人身攻击回怼：分析→策略→语境关联（机制级，非模板）----
class TestAttackStrategy(unittest.TestCase):
    def test_A51_no_self_deprecating_ack(self):
        """A. '你真丑'：不得自嘲认下（'我丑我自豪/对，我就是丑'）。"""
        r = Mouthpiece().respond("你真丑")
        for ln in r.replies:
            self.assertFalse(any(x in ln for x in ("我丑我自豪", "我丑我骄傲", "对，我就是", "我就是丑", "对啊我")),
                             f"出现自嘲认下：{ln}")
            self.assertFalse(any(x in ln for x in ("你也丑", "你比我还丑")), f"硬找对称：{ln}")

    def test_B52_no_unrelated_counter(self):
        """B. '你怎么这么矮？'：不得出现'你这么高怎么…'这类无关联反击。"""
        for q in ("你怎么这么矮？", "你真矮"):
            r = Mouthpiece().respond(q)
            for ln in r.replies:
                self.assertNotIn("你这么高", ln, f"出现无关联反击：{ln}")
                self.assertFalse(any(x in ln for x in ("你也矮", "你比我还矮")))

    def test_C53_no_mechanical_rich_counter(self):
        """C. '你真穷'：不得机械套'你这么有钱怎么…'（无优势+无共享语境时不硬造）。"""
        r = Mouthpiece().respond("你真穷")
        for ln in r.replies:
            self.assertNotIn("你这么有钱", ln, f"机械套有钱反差：{ln}")

    def test_D54_relation_huanshi(self):
        """D. '你是不是关系户？'：能给出可用回怼（不解释/反问/反向）。"""
        r = Mouthpiece().respond("你是不是关系户？")
        self.assertTrue(r.replies)

    def test_E55_refuse_advantage_premise(self):
        """E. '你学历这么高，怎么混成这样？'：拆'优势必然有好结果'前提，非机械复制。"""
        r = Mouthpiece().respond("你学历这么高，怎么混成这样？")
        for ln in r.replies:
            self.assertFalse(any(x in ln for x in ("你学历", "你也", "你比我还")), f"机械套模板：{ln}")
        self.assertTrue(any("标准" in ln or "评判" in ln or "给我自己" in ln for ln in r.replies),
                        f"未拆优势前提：{r.replies}")

    def test_F56_not_copy_across_scenarios(self):
        """F. 工作好vs学历高：不能给同一句，且都要自然成立、无AI劝架。"""
        r1 = Mouthpiece().respond("你学历这么高，怎么混成这样？")
        r2 = Mouthpiece().respond("你工作这么好，怎么还住这么差？")
        self.assertNotEqual(r1.replies, r2.replies, "两个不同优势场景输出不能雷同")
        for ln in r1.replies + r2.replies:
            self.assertFalse(any(w in ln for w in ("我理解", "你可以尝试", "建议你", "心平气和")))

    def test_G57_fallback_when_no_valid_point(self):
        """G. 无有效反击点时：给自然简短收尾，不为狠硬造逻辑。"""
        # 攻击含"无共享语境、无优势、难关联"的怪异场景 → 应仍能给出短句
        r = Mouthpiece().respond("他阴阳我名字起得怪")
        self.assertTrue(r.replies)
        for ln in r.replies:
            self.assertLessEqual(len(ln), 40)

    def test_58_new_unseen_scenarios_generalize(self):
        """全新未见过场景（用户未提供）也能按底层策略生成，非关键词→答案。"""
        unseen = [
            "相亲对象说我条件一般怎么回",
            "同事嫌我做事慢吞吞怎么办",
            "亲戚说我工作不如他儿子怎么接",
            "网友杠我没文化",
            "领导说我长得太嫩不像能扛事",
        ]
        for q in unseen:
            r = Mouthpiece().respond(q)
            self.assertTrue(r.replies, f"无回答：{q}")
            for ln in r.replies:
                self.assertFalse(any(w in ln for w in ("我理解", "你可以尝试", "心平气和", "每个人都有自己的看法")),
                                 f"AI劝架腔：{ln}")


# ---- 本次 Core 修复：攻击前提隔离（任何特征都不默认接受前提/不认下/不暴露内部） ----
class TestPremiseIsolation(unittest.TestCase):
    CASES = ["他骂我脚臭怎么回", "你脚臭。", "你口臭。", "你秃头。", "你怎么这么矮。", "你这么穷。",
             "你学历这么高怎么工资这么低。", "你都这个年龄了还没对象。", "你手机怎么这么便宜。",
             "你穿得这么土。", "你能力这么差怎么还能当负责人。"]
    INTERNAL = ("知识库", "case", "scope", "版本", "来源", "博主", "检索", "匹配分", "fallback",
                "引擎", "维度", "规则库", "规则", "策略", "招式", "provider", "embedding",
                "score", "KB_AVAILABLE", "工作流", "内部", "索引", "候选")

    def _replies(self, q):
        return Mouthpiece().respond(q).user_view().replies

    def test_59_no_premise_confirmation(self):
        """不得隐含确认攻击前提（'X能治/洗洗就好/我X怎么了/我就是X/我X也没'整类禁止）。"""
        for q in self.CASES:
            for ln in self._replies(q):
                self.assertFalse(any(w in ln for w in
                    ("能治", "能改", "洗洗", "治治", "改改", "我就是", "我承认", "也没", "不是故意", "自豪", "骄傲")),
                    f"隐含认下攻击前提：{q} → {ln}")

    def test_60_no_selfdep_or_forced_symmetry(self):
        """不自嘲认下、不硬找'你也X'对称。"""
        for q in self.CASES:
            for ln in self._replies(q):
                self.assertFalse(ln.startswith("对") and ("我就是" in ln or "我承认" in ln), f"自嘲认下：{q}→{ln}")
                self.assertFalse(any(x in ln for x in ("你也", "你比我还")), f"硬找对称：{q}→{ln}")

    def test_61_no_internal_leak(self):
        """任何特征攻击都绝不暴露知识库/引擎/规则/策略等内部词。"""
        for q in self.CASES:
            for ln in self._replies(q):
                self.assertFalse(any(w in ln for w in self.INTERNAL), f"内部泄漏：{q}→{ln}")

    def test_62_short_natural_and_present(self):
        for q in self.CASES:
            reps = self._replies(q)
            self.assertTrue(reps, f"无回答：{q}")
            for ln in reps:
                self.assertLessEqual(len(ln), 40, f"过长：{q}→{ln}")

    def test_63_not_just_one_example_fixed(self):
        """同一类问题换措辞/换特征都能处理（机制级，不是单例补丁）。"""
        variants = ["他说我脚臭", "室友天天说我脚臭", "同事阴阳我口臭怎么办", "她嘲笑我秃"]
        for q in variants:
            self.assertTrue(self._replies(q), f"变体无回答：{q}")

    def test_77_s9_questions_source(self):
        """感知类攻击（气味/声音/审美）→ 质疑信息来源 + 回射感官，绝不认下。"""
        smelly = self._replies("同事说我脚臭，怎么回？") + self._replies("他说我口臭，怎么怼？")
        for ln in smelly:
            self.assertTrue(any(w in ln for w in ("闻", "鼻子")), f"脚臭/口臭未质疑信息来源：{ln}")
            self.assertNotIn("我脚臭", ln)
            self.assertNotIn("我口臭", ln)
        voice = self._replies("他说我声音难听，怎么回？")
        for ln in voice:
            self.assertTrue(any(w in ln for w in ("听", "耳朵")), f"声音未回射耳朵：{ln}")
        ugly = self._replies("朋友说我长得丑，怎么回？")
        for ln in ugly:
            self.assertTrue(any(w in ln for w in ("看", "眼睛", "眼光", "滤镜")), f"审美未回射眼光：{ln}")

    def test_78_selfdep_case_blocked_in_attack(self):
        """被贬低时不直引自嘲/接梗型案例（'速冻矮子'=认下）；主动要玩梗时放行。"""
        reps = self._replies("他说我矮，怎么回？")
        for ln in reps:
            self.assertNotIn("矮子", ln, f"被贬低时直引自嘲句：{ln}")
            self.assertNotIn("床底", ln, f"被贬低时直引玩梗句：{ln}")
        playful = self._replies("他说我矮，我想接梗自嘲，怎么回？")
        self.assertTrue(playful, "主动玩梗场景无回答")

    def test_79_direct_cite_guard_keeps_good_cases(self):
        """前提隔离只拦认下句，不误杀优秀直引案例。"""
        for q, must in [("室友说我太敏感了，怎么怼？", "不敏感"),
                        ("别人说我是关系户，怎么回？", "靠关系"),
                        ("同事说讨厌我，怎么回？", "情绪问题")]:
            joined = "|".join(self._replies(q))
            self.assertIn(must, joined, f"优秀案例被误拦：{q} → {joined}")


class TestNewScenarioGeneralization(unittest.TestCase):
    """完全没见过的新语境（非换词）：按策略生成、不机械同文、无AI劝架。"""
    NEW = ["同事拿我单身的梗在群里开涮，怎么不动声色地还回去",
           "亲戚说我养猫不如养孩子，怎么接",
           "对象说我做饭难吃还天天说，怎么怼又不伤感情",
           "面试官阴阳我学校一般，怎么回",
           "楼下大爷说我头发少，怎么回"]

    def test_64_generates_for_all(self):
        for q in self.NEW:
            self.assertTrue(Mouthpiece().respond(q).user_view().replies, f"无回答：{q}")

    def test_65_no_ai_placate(self):
        for q in self.NEW:
            for ln in Mouthpiece().respond(q).user_view().replies:
                self.assertFalse(any(w in ln for w in ("我理解", "你可以尝试", "心平气和", "每个人都有自己的看法")),
                                 f"AI劝架腔：{q}→{ln}")

    def test_66_not_mechanically_same(self):
        """策略驱动：不同新场景不能都输出同一句话。"""
        outs = [tuple(Mouthpiece().respond(q).user_view().replies) for q in self.NEW]
        self.assertGreaterEqual(len(set(outs)), 3, f"输出过度同质：{outs}")

    def test_67_no_premise_confirm_in_new(self):
        for q in self.NEW:
            for ln in Mouthpiece().respond(q).user_view().replies:
                self.assertFalse(any(w in ln for w in ("能治", "洗洗", "我就是", "也没")), f"认下：{q}→{ln}")


# ---- 点对点原则：生成必须扣住具体攻击点，禁止"换成任何攻击都成立"的万金油句 ----
class TestPointToPoint(unittest.TestCase):
    # (query, 必须扣住的关键词)
    P2P = [
        ("他骂我脚臭怎么回", "脚底下"),
        ("你口臭。", "嘴边"),
        ("你秃头。", "秃"),
        ("你都这个年龄了还没对象。", "没对象"),
        ("同事拿我单身的梗在群里开涮，怎么不动声色地还回去", "单身"),
        ("亲戚说我养猫不如养孩子，怎么接", "养猫"),
        ("对象说我做饭难吃还天天说，怎么怼又不伤感情", "做饭"),
        ("面试官阴阳我学校一般，怎么回", "学校"),
        ("楼下大爷说我头发少，怎么回", "头发"),
        ("你学历这么高怎么工资这么低。", "学历"),
    ]

    def test_68_reply_holds_the_attack_point(self):
        """每条输出必须扣住被攻击的具体点（trait/触发内容），不能是通用句。"""
        for q, kw in self.P2P:
            reps = Mouthpiece().respond(q).user_view().replies
            self.assertTrue(any(kw in ln for ln in reps),
                            f"未扣住攻击点[{kw}]：{q} → {reps}")

    def test_69_not_generic_platitude(self):
        """'你眼光这么准/你挺闲'这类换成任何攻击都成立的句子，在攻击点上必须被替换。"""
        for q, kw in self.P2P:
            reps = Mouthpiece().respond(q).user_view().replies
            for ln in reps:
                if any(w in ln for w in ("眼光这么准", "判断力，我服")):
                    self.assertIn(kw, ln, f"万金油句未点对点：{q}→{ln}")


# ---- 偏好测评模式：进入/记录/画像/反馈/影响输出/一次选择不固化/HardRules优先 ----
class TestPreferenceEvaluation(unittest.TestCase):
    def setUp(self):
        from scripts import preference as pf
        self._pf = pf
        self._tmp = tempfile.mkdtemp(prefix="pref_test_")
        self._orig_env = os.environ.get("MOUTHPIECE_PREFS_DIR")
        os.environ["MOUTHPIECE_PREFS_DIR"] = self._tmp

    def tearDown(self):
        import shutil
        if self._orig_env is None:
            os.environ.pop("MOUTHPIECE_PREFS_DIR", None)
        else:
            os.environ["MOUTHPIECE_PREFS_DIR"] = self._orig_env
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_70_eval_triggers_and_bank(self):
        """触发词能进入测评；第一轮 30 题；10 个维度全覆盖。"""
        pf = self._pf
        for w in ("偏好测评", "让我选30个", "测一下我喜欢什么样的回答", "开始训练"):
            self.assertTrue(any(k in w for k in pf.EVAL_TRIGGERS), w)
        qs = pf.start_evaluation(1)
        self.assertEqual(len(qs), 30)
        dims = {q["dimension"] for q in qs}
        for d in ("coldness", "aggression", "sarcasm", "oral", "concise",
                  "emotion", "retort", "fact", "boundary", "realism"):
            self.assertIn(d, dims)

    def test_71_full_record_pipeline(self):
        """30 题记录 → 画像（带 confidence/sample_count）；全选 A 冷句 → coldness 上升。"""
        pf = self._pf
        qs = pf.start_evaluation(1)
        for q in qs:
            pf.record_choice(q["test_id"], "A")
        prof = pf.compute_profile(pf._load_records())
        self.assertEqual(prof["total_samples"], 30)
        self.assertGreater(prof["dims"]["coldness"]["sample_count"], 3)
        self.assertLessEqual(prof["dims"]["coldness"]["confidence"], 0.98)
        self.assertGreater(prof["dims"]["coldness"]["value"], 2.0)

    def test_72_one_choice_not_a_rule(self):
        """一次选择不固化：单题样本 confidence 必须很低。"""
        pf = self._pf
        qs = pf.start_evaluation(1)
        pf.record_choice(qs[0]["test_id"], "A")
        prof = pf.compute_profile(pf._load_records())
        self.assertLess(prof["dims"]["coldness"]["confidence"], 0.5)

    def test_73_dislike_counts(self):
        """'都不好'计入 dislikes。"""
        pf = self._pf
        pf.record_choice("pref_001", "都不好")
        prof = pf.compute_profile(pf._load_records())
        self.assertEqual(prof["dislikes"], 1)

    def test_74_feedback_signal(self):
        """'太客气了' → aggression 高于基线；'太狠了' → 回落。"""
        pf = self._pf
        pf.apply_feedback("太客气了")
        prof = pf.compute_profile(pf._load_records())
        self.assertGreater(prof["dims"]["aggression"]["value"], 2.5)
        pf.apply_feedback("太狠了")
        prof = pf.compute_profile(pf._load_records())
        self.assertLess(prof["dims"]["aggression"]["value"], 3.0)

    def test_75_round2_targets_uncertain_dims(self):
        """第二轮使用全新题库（不再重复第一轮题目），且优先样本少/不确定的维度（boundary 应排前）。"""
        pf = self._pf
        qs = pf.start_evaluation(1)
        for q in qs:
            if q["test_id"] in ("pref_017", "pref_018"):
                continue  # 跳过 boundary 题，使其 0 样本
            pf.record_choice(q["test_id"], "A")
        qs2 = pf.start_evaluation(2)
        dims = [q["dimension"] for q in qs2[:6]]
        self.assertIn("boundary", dims)
        # 第二轮题目必须是全新题库（不重复第一轮 test_id）
        ids1 = {q["test_id"] for q in qs}
        self.assertTrue(all(q["test_id"] not in ids1 for q in qs2[:6]),
                        "round2 不得复用第一轮题目")
        # 所有题 4 候选且带关系字段
        self.assertTrue(all(len(q["answers"]) == 4 for q in qs2[:6]))
        self.assertTrue(all(q.get("relation") for q in qs2[:6]))

    def test_76_profile_affects_output_within_hard_rules(self):
        """高攻击画像 → 强句排前；极简画像 → 条数变少；但认下前提的句子仍被拦。"""
        pf = self._pf
        from scripts.mouthpiece import Mouthpiece
        # 高攻击画像（写入当前偏好目录的 profile.json，load_profile 动态读取）
        _prof = os.path.join(self._tmp, "profile.json")
        with open(_prof, "w", encoding="utf-8") as f:
            json.dump({"dims": {"aggression": {"value": 4.2, "confidence": 0.9},
                                "concise": {"value": 2.0, "confidence": 0.9}}}, f, ensure_ascii=False)
        reps_agg = Mouthpiece().respond("他骂我脚臭怎么回").user_view().replies
        self.assertFalse(any("脚臭能治" in x or "洗洗就好" in x for x in reps_agg), "Hard Rule 仍须拦截认下句")
        # 极简画像（覆盖写）
        with open(_prof, "w", encoding="utf-8") as f:
            json.dump({"dims": {"aggression": {"value": 2.0, "confidence": 0.9},
                                "concise": {"value": 4.8, "confidence": 0.9}}}, f, ensure_ascii=False)
        reps_short = Mouthpiece().respond("他骂我脚臭怎么回").user_view().replies
        self.assertLessEqual(len(reps_short), 2)
        # 无画像（删除，回落默认）时条数不受限
        if os.path.exists(_prof):
            os.remove(_prof)
        reps_none = Mouthpiece().respond("他骂我脚臭怎么回").user_view().replies
        self.assertGreater(len(reps_none), len(reps_short))

    def test_84_lover_scene_never_retorts(self):
        """恋人情感话题必须'接住'（认错+给确定性），绝不回怼（用户红线：该认真时不许回怼）。"""
        from scripts.mouthpiece import Mouthpiece
        for q in ("对象说我不爱他了，怎么回？", "对象说我最近都不陪她，怎么回？", "女朋友说我敷衍她，怎么回？"):
            reps = Mouthpiece().respond(q).user_view().replies
            self.assertTrue(reps, f"无回答：{q}")
            for ln in reps:
                self.assertFalse(any(w in ln for w in ("挺闲", "说三道四", "操心", "没意思", "碍着")),
                                 f"恋人场景串台成回怼：{q}→{ln}")
        joined = "|".join(Mouthpiece().respond("对象说我不爱他了，怎么回？").user_view().replies)
        self.assertTrue(any(w in joined for w in ("我改", "没有不爱你", "心里一直有你")),
                        f"恋人场景未给出确定性接住：{joined}")

    def test_85_leader_gets_solution_candidate(self):
        """领导攻击场景：候选含'解决问题型'句（认错→复盘→给方案），且不横。"""
        from scripts.mouthpiece import Mouthpiece
        reps = Mouthpiece().respond("领导说我这么点事都做不好，怎么回？").user_view().replies
        joined = "|".join(reps)
        self.assertTrue(any(w in joined for w in ("复盘", "解决方案", "明天给您")),
                        f"领导场景缺解决问题型候选：{joined}")
        for ln in reps:
            self.assertFalse(any(w in ln for w in ("你行不行", "你能力", "废物")), f"领导场景过横：{ln}")

    def test_86_ex_gets_harsher_line(self):
        """前任场景追加更狠句（用户偏好：前任/旧情人放开强度）。"""
        from scripts.mouthpiece import Mouthpiece
        joined = "|".join(Mouthpiece().respond("前任说我离开他不行，怎么回？").user_view().replies)
        self.assertIn("越来越会酸", joined, f"前任场景缺狠句：{joined}")

    def test_87_profile_persisted_with_rounds(self):
        """三轮测评画像已落盘（>=55 样本），供生成链路读取。"""
        import os as _os
        import json as _json
        real = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                             "memory", "users", "default", "preferences", "profile.json")
        self.assertTrue(_os.path.exists(real), "profile.json 未落盘")
        with open(real, encoding="utf-8") as f:
            prof = _json.load(f)
        self.assertGreaterEqual(prof.get("total_samples", 0), 55,
                                f"profile 未含三轮样本：{prof.get('total_samples')}")

    def test_88_sibengfan_on_slander(self):
        """诋毁/造谣/背后说坏话 → 私生饭句式（把对方放粉丝低姿态+点破造谣），绝不接住诋毁。"""
        from scripts.attack import attack_generate
        for q in ("他造谣我怎么回", "同事又背后说我坏话怎么怼", "有人污蔑我怎么回"):
            reps = attack_generate(q, "平级")
            joined = "|".join(reps)
            self.assertIn("私生饭", joined, f"诋毁场景缺私生饭句式：{joined}")
            self.assertFalse(any("这么了解我" in x for x in reps), "不得接住对方诋毁")

    def test_89_fanjiuzhang_on_repeat(self):
        """对方惯犯（又/老是/经常/每次）重复攻击 → 翻旧账句式（抓重复+摆证据）。"""
        from scripts.attack import attack_generate
        for q in ("他老是说我胖怎么回", "同事又拿我学历说事怎么怼", "他每次都这样说我怎么回"):
            joined = "|".join(attack_generate(q, "平级"))
            self.assertIn("新词", joined, f"惯犯场景缺翻旧账句式：{joined}")

    def test_90_no_self_lowering_rule(self):
        """怼人立场规则：回怼句禁止把自己放低位（我说不过你/我配不上/我不如你），且不误伤'你怕说不过我'。"""
        from scripts.attack import _no_self_lowering
        for bad in ("我是说不过你", "我配不上你", "我确实不如他", "我说不赢他", "我吵不过你", "我输给你"):
            self.assertFalse(_no_self_lowering(bad), f"应拦截自降句：{bad}")
        for ok in ("你是怕当面说不过我？", "我离开你行不行不知道", "别研究我怎么进来的"):
            self.assertTrue(_no_self_lowering(ok), f"不应拦截：{ok}")


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---- 招式驱动生成（凌晨方案核心落地）----
class TestMoveDrivenGeneration(unittest.TestCase):
    def test_49_unseen_scenario_move_driven_not_plain_ai(self):
        """知识库没覆盖的新回怼场景 → move_generate，招式随场景变化、无AI劝架腔。"""
        r1 = Mouthpiece().respond("同事说我头发少怎么回")   # 质疑外貌 → 反问/不接
        r2 = Mouthpiece().respond("领导说这个你自己想办法怎么办")  # 甩锅给领导 → 捧杀/装傻
        self.assertEqual(r1.route, "move_generate")
        self.assertEqual(r2.route, "move_generate")
        self.assertNotEqual(r1.replies, r2.replies, "不同冲突应命中不同招式，输出不能雷同")
        for line in r1.replies + r2.replies:
            self.assertFalse(any(w in line for w in
                ("我理解", "你可以尝试", "从沟通角度", "建议你", "心平气和", "每个人都有自己的看法")),
                f"出现AI劝架腔：{line}")

    def test_50_no_false_positive_on_broad_trigger(self):
        """红线的'对不上号'回归：泛化'阴阳'query 不得直引'喂狗'案例。"""
        r = Mouthpiece().respond("朋友阴阳我没对象咋回")
        # 不得再直引喂狗案例（其 trigger 已收窄），也不得出现明显跑偏的直引
        joined = "".join(r.replies)
        self.assertNotIn("喂狗", joined)
        self.assertNotIn("狗叫", joined)
        self.assertNotEqual(r.route, "answer_direct", "不该直引无关案例")
