"""XBRLのCSV（タブ区切り・UTF-16）の読み取りを、実物と同じ形の小さなZIPで確かめる。"""
import io
import unittest
import zipfile

from scripts.jp_extract import extract, norm_date, norm_rel, num, read_rows
from scripts.jp_rules import decide

HEAD = ["要素ID", "項目名", "コンテキストID", "相対年度", "連結・個別", "期間・時点", "ユニットID", "単位", "値"]


def row(elem, ctx, rel, val, scope="その他"):
    return [f"jpcrp_cor:{elem}", "x", ctx, rel, scope, "期間", "JPYPerShares", "", val]


def make_zip(rows, audit=True):
    def tsv(rs):
        text = "\r\n".join("\t".join('"%s"' % c for c in r) for r in rs) + "\r\n"
        return ("﻿" + text).encode("utf-16-le")
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("XBRL_TO_CSV/jpcrp030000-asr-001_E02144-000_2026-03-31_01_2026-06-22.csv", tsv([HEAD] + rows))
        if audit:
            z.writestr("XBRL_TO_CSV/jpaud-aar-cn-001_E02144-000.csv",
                       tsv([HEAD, row("DividendPaidPerShareSummaryOfBusinessResults", "CurrentYearDuration", "当期", "999")]))
        z.writestr("readme.txt", "x")
    return b.getvalue()


NC = "CurrentYearDuration_NonConsolidatedMember"


class ExtractTest(unittest.TestCase):
    def softbank_rows(self):
        r = [row("DividendPaidPerShareSummaryOfBusinessResults", NC, "当期", "27.50"),
             row("DividendPaidPerShareSummaryOfBusinessResults", "Prior1YearDuration_NonConsolidatedMember", "前期", "44.00"),
             row("InterimDividendPaidPerShareSummaryOfBusinessResults", NC, "当期", "22.00")]
        for i, (d, dps, tot) in enumerate([("2025-10-22", "22.00", "31342000000"), ("2026-06-24", "5.50", "31344000000")], 1):
            c = f"FilingDateInstant_Row{i}Member"
            r += [row("DateOfResolutionDividendsOfSurplus", c, "提出日時点", d),
                  row("DividendPerShareDividendsOfSurplus", c, "提出日時点", dps),
                  row("TotalAmountOfDividendsDividendsOfSurplus", c, "提出日時点", tot),
                  row("ResolutionDividendsOfSurplus", c, "提出日時点", "取締役会決議")]
        r += [row("TotalNumberOfIssuedSharesSummaryOfBusinessResults", "CurrentYearInstant_NonConsolidatedMember", "当期末", "5698909091"),
              row("TotalNumberOfIssuedSharesSummaryOfBusinessResults", "Prior1YearInstant_NonConsolidatedMember", "前期末", "1465000000"),
              row("NetSales", NC, "当期", "100")]
        return r

    def test_read_rows_skips_audit_files_and_decodes_utf16(self):
        rows = read_rows(make_zip(self.softbank_rows()))
        self.assertTrue(rows)
        self.assertNotIn("999", [r[8] for r in rows])

    def test_extract_softbank_group(self):
        e = extract(read_rows(make_zip(self.softbank_rows())))
        self.assertEqual(e.annual, 27.5)
        self.assertEqual(e.interim, 22.0)
        self.assertEqual([r.date for r in e.table], ["2025-10-22", "2026-06-24"])
        self.assertEqual(e.table[1].dps, 5.5)
        self.assertEqual(e.issued, {"当期": 5698909091.0, "前期": 1465000000.0})
        self.assertTrue(e.table_sane)

    def test_end_to_end_gives_rebuilt_dividend(self):
        d = decide(extract(read_rows(make_zip(self.softbank_rows()))))
        self.assertEqual(d.flag, "split_mixed")
        self.assertAlmostEqual(d.annual, 11.0, delta=0.01)

    def test_duplicate_dates_make_the_table_unsane(self):
        rows = [row("DividendPaidPerShareSummaryOfBusinessResults", NC, "当期", "25.0")]
        for i, (d, dps) in enumerate([("2026-05-15", "25.0"), ("2026-05-15", "3900000.0")], 1):
            c = f"FilingDateInstant_Row{i}Member"
            rows += [row("DateOfResolutionDividendsOfSurplus", c, "提出日時点", d),
                     row("DividendPerShareDividendsOfSurplus", c, "提出日時点", dps)]
        self.assertFalse(extract(read_rows(make_zip(rows))).table_sane)

    def test_no_dividend_dash_is_none(self):
        e = extract(read_rows(make_zip([row("DividendPaidPerShareSummaryOfBusinessResults", NC, "当期", "－")])))
        self.assertIsNone(e.annual)

    def test_consolidated_scope_column_is_not_used_to_filter(self):
        # 「連結・個別」の列は「その他」になる。「連結」で絞ってはいけない
        e = extract(read_rows(make_zip([row("DividendPaidPerShareSummaryOfBusinessResults", NC, "当期", "95", scope="その他")])))
        self.assertEqual(e.annual, 95.0)

    def test_helpers(self):
        self.assertEqual(num("1,234.5"), 1234.5)
        self.assertIsNone(num("－"))
        self.assertIsNone(num(None))
        self.assertEqual(norm_date("2026年6月5日"), "2026-06-05")
        self.assertEqual(norm_date("2026-06-05"), "2026-06-05")
        self.assertEqual(norm_rel("前々期末"), "前々期")
        self.assertEqual(norm_rel("当期"), "当期")


if __name__ == "__main__":
    unittest.main()
