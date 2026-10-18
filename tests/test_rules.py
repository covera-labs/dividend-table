"""分割の扱いの規則を、試運転（2026/10/18）で実際に見た数字で確かめる。"""
import unittest

from scripts.jp_extract import Extract, TableRow
from scripts.jp_rules import decide, issued_ratio, table_ratio


def ex(annual, interim, table, cur=None, prev=None, sane=True):
    return Extract(annual=annual, interim=interim, table=[TableRow(*t) for t in table], table_sane=sane,
                   issued={k: v for k, v in (("当期", cur), ("前期", prev)) if v})


class RulesTest(unittest.TestCase):
    def test_normal_toyota(self):
        d = decide(ex(95, 45, [("2025-11-05", 45, 586.527e9), ("2026-05-08", 50, 651.697e9)], 13.0e9, 13.0e9))
        self.assertTrue(d.include)
        self.assertIsNone(d.flag)
        self.assertEqual(d.annual, 95)
        self.assertEqual([p["k"] for p in d.payments], ["i", "f"])
        self.assertEqual([p["d"] for p in d.payments], ["2025-11-05", "2026-05-08"])

    def test_normal_without_table_takeda(self):
        d = decide(ex(200, 100, [], 1.5e9, 1.5e9))
        self.assertTrue(d.include)
        self.assertIsNone(d.flag)
        self.assertEqual(d.payments, [])

    def test_mixed_softbank_group_is_rebuilt(self):
        d = decide(ex(27.5, 22.0, [("2025-10-22", 22.0, 31.342e9), ("2026-06-24", 5.5, 31.344e9)],
                      5.7e9, 1.465e9))
        self.assertTrue(d.include)
        self.assertEqual(d.flag, "split_mixed")
        self.assertAlmostEqual(d.annual, 11.0, delta=0.01)
        self.assertEqual(d.annual_raw, 27.5)
        self.assertAlmostEqual(d.interim, 5.5, delta=0.01)
        self.assertAlmostEqual(d.payments[1]["v"], 5.5, delta=0.01)

    def test_mixed_hapinet_style_two_to_one(self):
        # 中間25円（分割前）＋期末80円（分割後）。株数は 5,000万株 → 1億株
        d = decide(ex(105, 25, [("2025-11-13", 25, 1.25e9), ("2026-06-18", 80, 8.0e9)], 2.0e7 * 5, 5.0e7))
        self.assertEqual(d.flag, "split_mixed")
        self.assertAlmostEqual(d.annual, 92.5, delta=0.01)

    def test_split_year_but_same_basis_yodoko(self):
        # 分割は起きたが、中間も期末も分割後の基準。当期の年間はそのまま使える
        d = decide(ex(91, 20, [("2025-11-05", 20, 2.0e9), ("2026-05-11", 71, 7.1e9)], 4.91e8, 1.0e8))
        self.assertTrue(d.include)
        self.assertEqual(d.flag, "split_year")
        self.assertEqual(d.annual, 91)

    def test_split_year_single_payment(self):
        d = decide(ex(40, None, [("2026-06-18", 40, 1e9)], 2.04e7, 1.0e7))
        self.assertTrue(d.include)
        self.assertEqual(d.flag, "split_year")

    def test_split_year_with_interim_but_table_cannot_tell_is_excluded(self):
        # 年間22＝中間8＋期末14。表には期末の1行だけ。分割の年なので、混ざっているか分からない
        d = decide(ex(22, 8, [("2026-05-15", 14, 1e9)], 2.0e7, 1.0e7))
        self.assertFalse(d.include)

    def test_no_dividend(self):
        self.assertFalse(decide(ex(None, None, [], 1e7, 1e7)).include)
        self.assertFalse(decide(ex(0, None, [], 1e7, 1e7)).include)

    def test_absurd_value_is_excluded(self):
        self.assertFalse(decide(ex(150000, None, [], 1e7, 1e7)).include)

    def test_unreadable_table_is_ignored_when_issued_is_normal(self):
        # メイコー型: 同じ日付の行があり、極端な1株配当が入っている
        d = decide(ex(115, 45, [("2025-10-27", 45, 1e9), ("2025-10-27", 2256164.4, 1e9),
                                ("2026-05-13", 70, 1e9)], 4.0e7, 4.0e7, sane=False))
        self.assertTrue(d.include)
        self.assertIsNone(d.flag)
        self.assertEqual(d.payments, [])  # 読めない表の決議日は配らない

    def test_issued_missing_is_included_with_unchecked_flag(self):
        d = decide(ex(50, 20, [("2025-11-01", 20, 1e9), ("2026-05-01", 30, 1.5e9)]))
        self.assertTrue(d.include)
        self.assertEqual(d.flag, "unchecked")

    def test_issued_normal_but_table_ratio_large_is_flagged_check(self):
        d = decide(ex(60, 20, [("2025-11-01", 20, 1e9), ("2026-05-01", 40, 4e9)], 1e7, 1e7))
        self.assertFalse(d.include)
        self.assertEqual(d.flag, "check")

    def test_rebuild_refused_when_table_sum_does_not_match(self):
        d = decide(ex(100, 40, [("2025-11-01", 40, 1e9), ("2026-05-01", 40, 4e9)], 4e7, 1e7))
        self.assertFalse(d.include)

    def test_rebuild_refused_when_total_is_missing(self):
        d = decide(ex(80, 40, [("2025-11-01", 40, 1e9), ("2026-05-01", 40, None)], 4e7, 1e7))
        self.assertFalse(d.include)

    def test_ratios(self):
        e = ex(1, None, [("a", 22.0, 31.342e9), ("b", 5.5, 31.344e9)], 5.7e9, 1.465e9)
        self.assertAlmostEqual(table_ratio(e), 4.0, delta=0.01)
        self.assertAlmostEqual(issued_ratio(e), 3.89, delta=0.01)


if __name__ == "__main__":
    unittest.main()
