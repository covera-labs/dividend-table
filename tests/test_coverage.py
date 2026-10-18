"""取り込みの網羅率の集計を、小さな会社一覧で確かめる。"""
import os
import tempfile
import unittest

from scripts.coverage_report import read_listed, report

HEAD = "ダウンロード実行日,2026年10月18日現在,件数,5件\n"
COLS = "ＥＤＩＮＥＴコード,提出者種別,上場区分,連結の有無,資本金,決算日,提出者名,提出者名（英字）,提出者名（ヨミ）,所在地,提出者業種,証券コード,提出者法人番号\n"


def line(code, kind="内国法人・組合", listed="上場", name="会社", closing="3月31日", industry="製造業", sec="72030"):
    return f"{code},{kind},{listed},有,100,{closing},{name},,,東京,{industry},{sec},1\n"


class CoverageTest(unittest.TestCase):
    def make(self, body):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "codes.csv")
        with open(path, "wb") as f:
            f.write((HEAD + COLS + body).encode("cp932"))
        return path

    def test_read_listed_filters(self):
        path = self.make(line("E1", sec="72030") + line("E2", listed="非上場", sec="11110") + line("E3", kind="外国法人・組合", sec="22220")
                         + line("E4", name="日本ビルファンド投資法人", sec="89510") + line("E5", sec="") + line("E6", sec="297A0", closing="12月31日"))
        listed = read_listed(path)
        self.assertEqual(sorted(listed), ["297A", "7203"])
        self.assertEqual(listed["297A"]["closing"], "12月31日")

    def test_report_counts(self):
        listed = {"7203": {"closing": "3月31日"}, "1111": {"closing": "3月31日"}, "2222": {"closing": "12月31日"}, "3333": {"closing": "9月30日"}}
        entries = {"7203": {"include": True, "reason": ""}, "1111": {"include": False, "reason": "無配"}, "9999": {"include": True, "reason": ""}}
        r = report(listed, entries)
        self.assertEqual((r["listed"], r["included"], r["excluded"], r["missing"]), (4, 1, 1, 2))
        self.assertEqual(r["by_reason"], {"無配": 1})
        self.assertEqual(r["missing_by_closing"], {"12月31日": 1, "9月30日": 1})


if __name__ == "__main__":
    unittest.main()
