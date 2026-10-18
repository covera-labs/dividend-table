"""米国株の取り込み（SECクライアント・会社ごとの値の決め方・全体の流れ）を確かめる。"""
import http.server
import json
import os
import tempfile
import threading
import unittest

from scripts import build_us
from scripts.sec import Client, SecError, clean_contact
from scripts.us_rules import DECLARED, PAID, apply_overrides, build_section, choose, find_orphans, latest_per_cik


def row(cik, val, start="2024-10-01", end="2025-09-30"):
    return {"cik": cik, "val": val, "start": start, "end": end, "accn": "x", "entityName": "X", "loc": "US-CA"}


class ChooseTest(unittest.TestCase):
    def test_declared_only_apple(self):
        self.assertEqual(choose({"end": "2025-09-27", "val": 1.02}, None), (1.02, "2025-09-27", "declared", None))

    def test_paid_only_coca_cola(self):
        self.assertEqual(choose(None, {"end": "2025-12-31", "val": 2.04}), (2.04, "2025-12-31", "paid", None))

    def test_both_close_uses_declared(self):
        v, end, tag, flag = choose({"end": "2025-12-31", "val": 4.46}, {"end": "2025-12-31", "val": 4.43})
        self.assertEqual((v, tag, flag), (4.46, "declared", None))

    def test_monthly_value_in_annual_field_uses_the_larger(self):
        # LTC型: 宣言した額は月ごとの0.19、実際に払った額は年2.28（12倍）
        v, end, tag, flag = choose({"end": "2025-12-31", "val": 0.19}, {"end": "2025-12-31", "val": 2.28})
        self.assertEqual((v, tag, flag), (2.28, "paid", "freq_fixed"))

    def test_quarterly_value_in_annual_field_uses_the_larger(self):
        v, end, tag, flag = choose({"end": "2025-12-31", "val": 1.2}, {"end": "2025-12-31", "val": 0.3})
        self.assertEqual((v, tag, flag), (1.2, "declared", "freq_fixed"))

    def test_unexplained_difference_is_conflict_and_uses_declared(self):
        v, end, tag, flag = choose({"end": "2025-12-31", "val": 0.3}, {"end": "2025-12-31", "val": 2.2})
        self.assertEqual((v, tag, flag), (0.3, "declared", "conflict"))

    def test_different_fiscal_years_use_the_newer(self):
        v, end, tag, flag = choose({"end": "2025-12-31", "val": 3.0}, {"end": "2024-12-31", "val": 5.0})
        self.assertEqual((v, end, tag, flag), (3.0, "2025-12-31", "declared", None))
        v, end, tag, flag = choose({"end": "2024-06-30", "val": 3.0}, {"end": "2025-06-30", "val": 5.0})
        self.assertEqual((v, end, tag, flag), (5.0, "2025-06-30", "paid", None))

    def test_none(self):
        self.assertIsNone(choose(None, None))


class SectionTest(unittest.TestCase):
    def test_latest_year_wins_and_partial_periods_are_ignored(self):
        best = latest_per_cik([[row(1, 1.0, "2024-01-01", "2024-12-31"), row(2, 9.0, "2025-10-01", "2025-12-31")],
                               [row(1, 1.2, "2025-01-01", "2025-12-31")]])
        self.assertEqual(best, {1: {"end": "2025-12-31", "val": 1.2, "name": "X"}})  # 3か月だけの値(cik 2)は使わない

    def test_zero_negative_and_huge_values_are_ignored(self):
        self.assertEqual(latest_per_cik([[row(1, 0), row(2, -1), row(3, 99999), row(4, 2.0)]]), {4: {"end": "2025-09-30", "val": 2.0, "name": "X"}})

    def test_build_section_maps_all_tickers_and_drops_unknown_and_stale(self):
        declared = [[row(320193, 1.02, "2024-09-29", "2025-09-27"), row(111, 5.0, "2024-01-01", "2024-12-31"),
                     row(222, 1.0)]]
        tickers = {320193: ["AAPL"], 111: ["OLD"], 1652044: ["GOOGL", "GOOG"]}
        paid = [[row(1652044, 0.8, "2025-01-01", "2025-12-31")]]
        out = build_section(declared, paid, tickers, "2026-10-18")
        self.assertEqual(sorted(out), ["AAPL", "GOOG", "GOOGL"])   # OLD は古い、222 はティッカーが無い
        self.assertEqual(out["AAPL"], {"fy": "2025-09-27", "annual": 1.02, "ccy": "USD", "tag": "declared"})
        self.assertEqual(out["GOOG"]["tag"], "paid")


class OverrideTest(unittest.TestCase):
    def test_override_moves_a_ticker_to_the_cik_that_has_the_dividend_record(self):
        # 新しいCIK(2)にティッカー XOM が付いているが、配当の記録は、古いCIK(1)にある
        paid = [[row(1, 4.0, "2025-01-01", "2025-12-31")]]
        tickers = {2: ["XOM"], 3: ["KO"]}
        self.assertEqual(build_section([], paid, tickers, "2026-10-18"), {})
        out = build_section([], paid, tickers, "2026-10-18", overrides={"XOM": 1})
        self.assertEqual(out["XOM"]["annual"], 4.0)
        self.assertEqual(apply_overrides(tickers, {"XOM": 1}), {2: [], 3: ["KO"], 1: ["XOM"]})

    def test_orphans_lists_companies_with_dividends_but_no_ticker(self):
        paid = [[row(1, 4.0, "2025-01-01", "2025-12-31"), row(9, 1.0, "2025-01-01", "2025-12-31")]]
        self.assertEqual(find_orphans([], paid, {9: ["ZZZ"]}), [(1, "X")])


class ClientTest(unittest.TestCase):
    def serve(self, script):
        state = {"n": 0, "uas": []}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                state["n"] += 1
                state["uas"].append(self.headers.get("User-Agent"))
                code, body = script(state["n"], self.path)
                self.send_response(code)
                self.end_headers()
                self.wfile.write(body)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return f"http://127.0.0.1:{srv.server_port}", state

    def test_contact_is_sent_as_user_agent_and_cleaned(self):
        base, st = self.serve(lambda n, p: (200, json.dumps({"data": [row(1, 1.0)]}).encode()))
        c = Client("Covera Labs covera@example.com\r\n", sleep=0, backoff=0, frames_url=base)
        self.assertEqual(len(c.frame(DECLARED, 2025)), 1)
        self.assertEqual(st["uas"][0], "Covera Labs covera@example.com")
        self.assertEqual(clean_contact(" a b\r\n"), "a b")

    def test_empty_contact_is_rejected(self):
        with self.assertRaises(SecError):
            Client("\r\n")

    def test_retry_on_429_then_ok(self):
        base, st = self.serve(lambda n, p: (429, b"") if n < 3 else (200, b'{"data": []}'))
        self.assertEqual(Client("c", sleep=0, backoff=0, frames_url=base).frame(PAID, 2025), [])
        self.assertEqual(st["n"], 3)

    def test_403_is_not_retried_and_message_has_no_contact(self):
        base, st = self.serve(lambda n, p: (403, b""))
        with self.assertRaises(SecError) as cm:
            Client("secret contact", sleep=0, backoff=0, frames_url=base).frame(PAID, 2025)
        self.assertNotIn("secret contact", str(cm.exception))
        self.assertEqual(st["n"], 1)

    def test_tickers_groups_by_cik(self):
        body = json.dumps({"0": {"cik_str": 1652044, "ticker": "GOOGL", "title": "A"},
                           "1": {"cik_str": 1652044, "ticker": "goog", "title": "A"},
                           "2": {"cik_str": 320193, "ticker": "AAPL", "title": "B"}}).encode()
        base, st = self.serve(lambda n, p: (200, body))
        self.assertEqual(Client("c", sleep=0, backoff=0, tickers_url=base).tickers(), {1652044: ["GOOGL", "GOOG"], 320193: ["AAPL"]})


class FakeClient:
    def __init__(self, frames, tickers):
        self.frames, self._tickers, self.calls = frames, tickers, []

    def frame(self, tag, year):
        self.calls.append((tag, year))
        return self.frames.get((tag, year), [])

    def tickers(self):
        return self._tickers


class BuildTest(unittest.TestCase):
    def test_run_writes_the_table_and_asks_two_years_for_two_tags(self):
        c = FakeClient({(DECLARED, 2026): [], (DECLARED, 2025): [row(320193, 1.02, "2024-09-29", "2025-09-27")],
                        (PAID, 2025): [row(21344, 2.04, "2025-01-01", "2025-12-31")]},
                       {320193: ["AAPL"], 21344: ["KO"]})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "us.json")
            section, orphans = build_us.run(c, "2026-10-18", path)
            self.assertEqual(orphans, [])
            self.assertEqual(sorted(section), ["AAPL", "KO"])
            self.assertEqual(sorted(c.calls), sorted([(DECLARED, 2026), (DECLARED, 2025), (PAID, 2026), (PAID, 2025)]))
            with open(path, encoding="utf-8") as f:
                self.assertEqual(sorted(json.load(f)), ["AAPL", "KO"])

    def test_refuses_to_overwrite_with_a_much_smaller_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "us.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({f"T{i}": {"fy": "2025-12-31", "annual": 1.0} for i in range(100)}, f)
            c = FakeClient({(DECLARED, 2025): [row(1, 1.0, "2025-01-01", "2025-12-31")]}, {1: ["AAA"]})
            with self.assertRaises(SecError):
                build_us.run(c, "2026-10-18", path)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(len(json.load(f)), 100)   # 前回の表のまま


if __name__ == "__main__":
    unittest.main()
