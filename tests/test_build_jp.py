"""日本株の表を作る流れ（取り込み・再開・新しい書類での置き換え・失敗の扱い）を、偽のクライアントで確かめる。"""
import json
import os
import tempfile
import unittest

from scripts.build_jp import run, summarize
from scripts.edinet import EdinetError
from tests.test_extract import make_zip, row, NC


def dividend_zip(annual, interim, table, cur_issued, prev_issued):
    rows = [row("DividendPaidPerShareSummaryOfBusinessResults", NC, "当期", str(annual))]
    if interim is not None:
        rows.append(row("InterimDividendPaidPerShareSummaryOfBusinessResults", NC, "当期", str(interim)))
    for i, (d, dps, tot) in enumerate(table, 1):
        c = f"FilingDateInstant_Row{i}Member"
        rows += [row("DateOfResolutionDividendsOfSurplus", c, "提出日時点", d),
                 row("DividendPerShareDividendsOfSurplus", c, "提出日時点", str(dps)),
                 row("TotalAmountOfDividendsDividendsOfSurplus", c, "提出日時点", str(tot))]
    rows += [row("TotalNumberOfIssuedSharesSummaryOfBusinessResults", "CurrentYearInstant_NonConsolidatedMember", "当期末", str(cur_issued)),
             row("TotalNumberOfIssuedSharesSummaryOfBusinessResults", "Prior1YearInstant_NonConsolidatedMember", "前期末", str(prev_issued))]
    return make_zip(rows, audit=False)


def doc(doc_id, sec, day, kind="120", name="会社", period_end="2026-03-31"):
    return {"docID": doc_id, "docTypeCode": kind, "ordinanceCode": "010", "secCode": sec, "csvFlag": "1",
            "withdrawalStatus": "0", "submitDateTime": f"{day} 15:00", "periodEnd": period_end, "filerName": name}


def dei(zip_args, fy_end):
    """決算期末（表紙情報）を足したZIP"""
    annual, interim, table, cur, prev = zip_args
    rows = [row("DividendPaidPerShareSummaryOfBusinessResults", NC, "当期", str(annual)),
            ["jpdei_cor:CurrentFiscalYearEndDateDEI", "当事業年度終了日", "FilingDateInstant", "提出日時点", "その他", "時点", "", "", fy_end],
            row("TotalNumberOfIssuedSharesSummaryOfBusinessResults", "CurrentYearInstant_NonConsolidatedMember", "当期末", str(cur)),
            row("TotalNumberOfIssuedSharesSummaryOfBusinessResults", "Prior1YearInstant_NonConsolidatedMember", "前期末", str(prev))]
    return make_zip(rows, audit=False)


class FakeClient:
    def __init__(self, lists, zips, fail=()):
        self.lists, self.zips, self.fail = lists, zips, set(fail)
        self.listed, self.fetched = [], []

    def list_documents(self, day):
        self.listed.append(day)
        return self.lists.get(day, [])

    def fetch_csv_zip(self, doc_id):
        self.fetched.append(doc_id)
        if doc_id in self.fail:
            raise EdinetError("取得に失敗しました")
        return self.zips.get(doc_id)


NORMAL = dividend_zip(95, 45, [("2025-11-05", 45, 586e9), ("2026-05-08", 50, 650e9)], 13e9, 13e9)
MIXED = dividend_zip(27.5, 22.0, [("2025-10-22", 22.0, 31.342e9), ("2026-06-24", 5.5, 31.344e9)], 5.7e9, 1.465e9)
NODIV = dividend_zip("－", None, [], 1e7, 1e7)


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "jp")

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, name):
        with open(os.path.join(self.dir, name), encoding="utf-8") as f:
            return json.load(f)

    def test_builds_entries_and_rebuilds_mixed_split(self):
        c = FakeClient({"2026-06-24": [doc("S1", "72030", "2026-06-24"), doc("S2", "99840", "2026-06-24"),
                                       doc("S3", "11110", "2026-06-24")]},
                       {"S1": NORMAL, "S2": MIXED, "S3": NODIV})
        stats, stopped, entries = run(c, self.dir, "2026-06-24", "2026-06-24", today="2026-10-18")
        self.assertIsNone(stopped)
        self.assertEqual(stats["fetched"], 3)
        self.assertTrue(entries["7203"]["include"])
        self.assertEqual(entries["7203"]["annual"], 95)
        self.assertEqual(entries["9984"]["flag"], "split_mixed")
        self.assertAlmostEqual(entries["9984"]["annual"], 11.0, delta=0.01)
        self.assertFalse(entries["1111"]["include"])
        self.assertEqual(sorted(self.read("processed.json")), ["S1", "S2", "S3"])
        self.assertEqual(self.read("days.json"), ["2026-06-24"])

    def test_second_run_skips_done_days_and_processed_docs(self):
        lists = {"2026-06-24": [doc("S1", "72030", "2026-06-24")]}
        run(FakeClient(lists, {"S1": NORMAL}), self.dir, "2026-06-24", "2026-06-24", today="2026-10-18")
        c2 = FakeClient(lists, {"S1": NORMAL})
        stats, _, _ = run(c2, self.dir, "2026-06-24", "2026-06-24", today="2026-10-18")
        self.assertEqual(c2.listed, [])
        self.assertEqual(c2.fetched, [])
        self.assertEqual(stats["days_skipped"], 1)

    def test_recent_days_are_listed_again_but_docs_are_not_fetched_twice(self):
        lists = {"2026-10-16": [doc("S1", "72030", "2026-10-16")]}
        run(FakeClient(lists, {"S1": NORMAL}), self.dir, "2026-10-16", "2026-10-16", today="2026-10-18")
        lists["2026-10-16"].append(doc("S2", "99840", "2026-10-16"))  # 遅れて出た書類
        c2 = FakeClient(lists, {"S1": NORMAL, "S2": MIXED})
        run(c2, self.dir, "2026-10-16", "2026-10-16", today="2026-10-18")
        self.assertEqual(c2.listed, ["2026-10-16"])
        self.assertEqual(c2.fetched, ["S2"])

    def test_budget_stops_and_resumes_where_it_left(self):
        lists = {"2026-06-24": [doc(f"S{i}", f"{1000 + i}0", "2026-06-24") for i in range(5)]}
        zips = {f"S{i}": NORMAL for i in range(5)}
        stats, stopped, _ = run(FakeClient(lists, zips), self.dir, "2026-06-24", "2026-06-24", max_docs=2, today="2026-10-18")
        self.assertEqual(stats["fetched"], 2)
        self.assertIn("上限", stopped)
        self.assertEqual(self.read("days.json"), [])  # まだ終わっていない日は、記録しない
        c2 = FakeClient(lists, zips)
        stats2, stopped2, entries = run(c2, self.dir, "2026-06-24", "2026-06-24", max_docs=10, today="2026-10-18")
        self.assertIsNone(stopped2)
        self.assertEqual(len(c2.fetched), 3)
        self.assertEqual(len(entries), 5)
        self.assertEqual(self.read("days.json"), ["2026-06-24"])

    def test_newer_filing_replaces_older_and_older_does_not_replace_newer(self):
        lists = {"2026-06-10": [doc("S1", "72030", "2026-06-10")],
                 "2026-07-01": [doc("S2", "72030", "2026-07-01", kind="130")]}
        zips = {"S1": NORMAL, "S2": dividend_zip(100, 50, [("2025-11-05", 50, 650e9), ("2026-05-08", 50, 650e9)], 13e9, 13e9)}
        _, _, entries = run(FakeClient(lists, zips), self.dir, "2026-06-10", "2026-07-01", today="2026-10-18")
        self.assertEqual(entries["7203"]["annual"], 100)  # 訂正で置き換わった
        self.assertEqual(entries["7203"]["docType"], "130")
        # 古い書類を、あとから処理しても、新しい結果は変わらない
        self.tmp2 = tempfile.TemporaryDirectory()
        d2 = os.path.join(self.tmp2.name, "jp")
        lists2 = {"2026-07-01": [doc("S2", "72030", "2026-07-01", kind="130")]}
        run(FakeClient(lists2, zips), d2, "2026-07-01", "2026-07-01", today="2026-10-18")
        _, _, entries2 = run(FakeClient({"2026-06-10": [doc("S1", "72030", "2026-06-10")]}, zips), d2,
                             "2026-06-10", "2026-06-10", today="2026-10-18")
        self.assertEqual(entries2["7203"]["annual"], 100)
        self.tmp2.cleanup()

    def test_failures_are_retried_later_and_stop_after_four_in_a_row(self):
        lists = {"2026-06-24": [doc(f"S{i}", f"{1000 + i}0", "2026-06-24") for i in range(6)]}
        zips = {f"S{i}": NORMAL for i in range(6)}
        c = FakeClient(lists, zips, fail={f"S{i}" for i in range(6)})
        stats, stopped, _ = run(c, self.dir, "2026-06-24", "2026-06-24", today="2026-10-18")
        self.assertEqual(stats["failed"], 4)
        self.assertIn("続けて", stopped)
        self.assertEqual(self.read("processed.json"), [])  # 失敗は「処理済み」にしない
        c2 = FakeClient(lists, zips)
        stats2, _, entries = run(c2, self.dir, "2026-06-24", "2026-06-24", today="2026-10-18")
        self.assertEqual(len(entries), 6)

    def test_no_csv_is_recorded_as_excluded(self):
        c = FakeClient({"2026-06-24": [doc("S1", "72030", "2026-06-24")]}, {})
        stats, _, entries = run(c, self.dir, "2026-06-24", "2026-06-24", today="2026-10-18")
        self.assertEqual(stats["no_csv"], 1)
        self.assertFalse(entries["7203"]["include"])

    def test_non_target_documents_are_ignored(self):
        other = doc("S9", "72030", "2026-06-24", kind="140")
        c = FakeClient({"2026-06-24": [other]}, {"S9": NORMAL})
        run(c, self.dir, "2026-06-24", "2026-06-24", today="2026-10-18")
        self.assertEqual(c.fetched, [])

    def test_weekends_are_not_listed(self):
        c = FakeClient({}, {})
        run(c, self.dir, "2026-10-17", "2026-10-18", today="2026-10-20")  # 土日
        self.assertEqual(c.listed, [])

    def test_correction_filing_without_period_end_takes_it_from_the_cover_information(self):
        lists = {"2026-07-01": [doc("S2", "72030", "2026-07-01", kind="130", period_end=None)]}
        zips = {"S2": dei((100, None, [], 13e9, 13e9), "2026-03-31")}
        _, _, entries = run(FakeClient(lists, zips), self.dir, "2026-07-01", "2026-07-01", today="2026-10-18")
        self.assertEqual(entries["7203"]["fy"], "2026-03-31")

    def test_correction_filing_without_any_period_inherits_the_previous_one(self):
        lists = {"2026-06-10": [doc("S1", "72030", "2026-06-10")],
                 "2026-07-01": [doc("S2", "72030", "2026-07-01", kind="130", period_end=None)]}
        zips = {"S1": NORMAL, "S2": dividend_zip(100, 50, [("2025-11-05", 50, 650e9), ("2026-05-08", 50, 650e9)], 13e9, 13e9)}
        _, _, entries = run(FakeClient(lists, zips), self.dir, "2026-06-10", "2026-07-01", today="2026-10-18")
        self.assertEqual(entries["7203"]["docType"], "130")
        self.assertEqual(entries["7203"]["fy"], "2026-03-31")

    def test_repair_fills_missing_fiscal_year_ends_once(self):
        from scripts.build_jp import repair_fy
        entries = {"7203": {"docId": "S2", "include": True, "fy": None}, "9984": {"docId": "S3", "include": True, "fy": "2026-03-31"},
                   "1111": {"docId": "S4", "include": True, "fy": None}}
        c = FakeClient({}, {"S2": dei((100, None, [], 1, 1), "2026-03-31"), "S4": NORMAL})
        self.assertEqual(repair_fy(c, entries), (1, 1))
        self.assertEqual(entries["7203"]["fy"], "2026-03-31")
        self.assertTrue(entries["1111"]["fyChecked"])      # 取れなかった会社は、印を付けて、やり直さない
        c2 = FakeClient({}, {})
        self.assertEqual(repair_fy(c2, entries), (0, 0))
        self.assertEqual(c2.fetched, [])

    def test_summarize(self):
        text = summarize({"7203": {"include": True, "flag": None, "reason": ""},
                          "9984": {"include": True, "flag": "split_mixed", "reason": ""},
                          "1111": {"include": False, "flag": None, "reason": "無配"}})
        self.assertIn("会社数 3", text)
        self.assertIn("split_mixed 1", text)
        self.assertIn("無配 1", text)


if __name__ == "__main__":
    unittest.main()
