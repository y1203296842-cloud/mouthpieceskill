"""tests/test_stance_gate.py — 立场矩阵 + 硬否决质检门。

运行：
  cd mouthpieceskill
  python3 -m tests.test_stance_gate

覆盖：
  立场：加活/贬低/打听/开涮/催生/认错/不必回 七类行为的 位置+下一步 判定
  质检门：编事实 / 替第三方承诺 / 立场反了 / 越边界 / 上位自降 / 不必回长篇 六道门
  兼容：门全关时输出必须与基线完全一致（零侵入）
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.quality_gate import gate, gate_one
from scripts.stance import detect_relation, judge
from scripts.mouthpiece import run


class TestStance(unittest.TestCase):
    def test_01_peer_assign_work_is_above(self):
        """同级的室友使唤你 → 你在上位，直接回，不自证。"""
        s = judge("室友让我帮她取快递")
        self.assertEqual(s["behavior"], "B_ADD_WORK")
        self.assertEqual(s["relation"], "peer")
        self.assertEqual(s["position"], "ABOVE")
        self.assertEqual(s["next"], "DIRECT")

    def test_02_senior_assign_work_is_at(self):
        """领导加活 → 平位，可谈条件，不硬刚。"""
        s = judge("领导让我今晚加个班")
        self.assertEqual(s["relation"], "senior")
        self.assertEqual(s["position"], "AT")
        self.assertEqual(s["next"], "CHOOSE")

    def test_03_deprecate_is_above(self):
        """同事贬低你 → 上位，直接回。"""
        s = judge("同事说我脚臭")
        self.assertEqual(s["behavior"], "B_DEPRECATE")
        self.assertEqual(s["position"], "ABOVE")

    def test_04_nosy_is_above(self):
        """打听工资/隐私 → 上位。"""
        s = judge("有人问我工资多少")
        self.assertEqual(s["behavior"], "B_NOSY")
        self.assertEqual(s["position"], "ABOVE")

    def test_05_own_fault_is_below(self):
        """确实是你漏了活 → 唯一允许低位的场景。"""
        s = judge("我把表格漏了，领导问我怎么回事")
        self.assertEqual(s["behavior"], "B_OWN_FAULT")
        self.assertEqual(s["position"], "BELOW")

    def test_06_short_ack_is_no_reply(self):
        """只回了个"收到" → 不必回。"""
        s = judge("同事说收到")
        self.assertEqual(s["behavior"], "B_SHORT_ACK")
        self.assertEqual(s["next"], "NO_REPLY")

    def test_07_conflict_blocks_short_ack(self):
        """带冲突信号的输入不能被误判成"不必回"。"""
        s = judge("领导说我方案不到位，还回了个收到你看着办")
        self.assertNotEqual(s["next"], "NO_REPLY")

    def test_08_family_is_not_senior(self):
        """长辈/父母归 family，不能当领导处理。"""
        self.assertEqual(detect_relation("我妈说我乱花钱"), "family")
        self.assertEqual(detect_relation("领导说我方案不行"), "senior")

    def test_09_ex_is_above(self):
        """前任回头 → 上位，不接旧账。"""
        s = judge("前任突然问我最近怎么样")
        self.assertEqual(s["relation"], "ex")
        self.assertEqual(s["position"], "ABOVE")


class TestGate(unittest.TestCase):
    def _ok(self, reply, query, stance=None):
        kept, blocked = gate([reply], query, stance or {})
        return bool(kept)

    def test_10_blocks_invented_fact(self):
        self.assertFalse(self._ok("我花了300块买的东西，你凭什么说不好", "有人问我工资多少"))

    def test_11_blocks_committing_others(self):
        self.assertFalse(self._ok("我同事会帮你把这事搞定", "领导让我今晚加个班"))

    def test_12_blocks_opposite_stance(self):
        self.assertFalse(self._ok("行，没问题，我帮你", "怎么拒绝室友老是让我带饭"))

    def test_13_blocks_self_downgrade(self):
        """上位/平位场景里"你说得对，我这水平配不上您"必须拦。"""
        st = judge("领导让我今晚加个班")
        self.assertFalse(self._ok("你说得对，我这水平确实配不上您这种“专业”。", "领导让我今晚加个班", st))

    def test_13b_below_must_offer_remedy(self):
        """理亏场景只给硬话不给补救/时间 → 拦；给了就放行。"""
        st = judge("领导说我这个方案做得不到位")
        self.assertFalse(self._ok("你这么在意别人这个的事，平时应该挺闲的。",
                                  "领导说我这个方案做得不到位", st))
        self.assertTrue(self._ok("这个确实没做到位，我复盘下原因，明天给您个解决方案。",
                                 "领导说我这个方案做得不到位", st))

    def test_14_allows_below_apology(self):
        """真理亏时认错必须放行（不能把正确的认错也拦掉）。"""
        st = judge("我把表格漏了，领导问我怎么回事")
        self.assertTrue(self._ok("这个确实没做到位，我复盘下原因，明天给您解决方案。", "我把表格漏了", st))

    def test_15_allows_firm_retort(self):
        """正常回怼不能被误杀。"""
        self.assertTrue(self._ok("你偷摸趴我脚底下闻了？别人都没闻到就你闻到了。", "同事说我脚臭"))

    def test_16_allows_concede_with_turn(self):
        """让步但立刻划界（有转折）放行。"""
        st = judge("室友让我帮她取快递")
        self.assertTrue(self._ok("你说得对，但这事不归我做。", "室友让我帮她取快递", st))

    def test_17_no_reply_keeps_one_short(self):
        """不必回的场合最多留 1 句。"""
        st = judge("同事说收到")
        kept, blocked = gate(["嗯。", "这话你自己站得住脚吗？", "先别急着给我下定义。"], "同事说收到", st)
        self.assertEqual(len(kept), 1)

    def test_18_gate_off_is_noop(self):
        """MOUTHPIECE_GATE=off 时完全不干预。"""
        os.environ["MOUTHPIECE_GATE"] = "off"
        try:
            kept, blocked = gate(["行，没问题，我帮你"], "怎么拒绝室友老是让我带饭", {})
            self.assertEqual(kept, ["行，没问题，我帮你"])
            self.assertEqual(blocked, [])
        finally:
            os.environ.pop("MOUTHPIECE_GATE", None)

    def test_19_single_check_disable(self):
        """按名关闭单道门后，其它门仍生效。"""
        os.environ["MOUTHPIECE_GATE_OFF"] = "opposite_stance"
        try:
            kept, _ = gate(["行，没问题，我帮你"], "怎么拒绝室友老是让我带饭", {})
            self.assertEqual(len(kept), 1)
        finally:
            os.environ.pop("MOUTHPIECE_GATE_OFF", None)

    def test_20_no_crash_on_weird_input(self):
        """畸形输入不该把质检打崩。"""
        ok, fails = gate_one("", "", "", "")
        self.assertTrue(ok or fails)


class TestIntegration(unittest.TestCase):
    def test_21_direct_cite_untouched(self):
        """高匹配直引不经过质检门（保真）。"""
        r = run("同事说我太敏感")
        if r.route == "answer_direct":
            self.assertEqual(r.gate_blocked, [])

    def test_22_gate_keeps_non_empty(self):
        """任何场景都不能输出空话术。"""
        for q in ("室友让我帮她取快递", "同事说收到", "有人问我工资多少", "领导让我今晚加个班"):
            r = run(q)
            self.assertTrue(r.user_view().replies, f"{q} 输出为空")

    def test_23_stance_attached(self):
        """生成类路由带上立场判定。"""
        r = run("室友让我帮她取快递")
        if r.route in ("move_generate", "pattern_generate", "style_generate"):
            self.assertIn(r.stance.get("position"), ("ABOVE", "AT", "BELOW", "CUE", "DEPENDS", "RARE"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
