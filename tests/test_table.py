"""配る表の組み立てと、公開の前の検査を確かめる。"""
import json
import os
import tempfile
import unittest

from scripts import publish
from scripts.table import build_document, build_jp_section, make_status, validate, write_public


def entry(**kw):
    base = {"include": True, "annual": 95, "annualRaw": 95, "interim": 45, "flag": None, "fy": "2026-03-31",
            "filed": "2026-06-10", "payments": [{"d": "2025-11-05", "v": 45, "k": "i"}, {"d": "2026-05-08", "v": 50, "k": "f"}]}
    base.update(kw)
    return base


class SectionTest(unittest.TestCase):
    def test_only_included_and_fresh_companies_are_listed(self):
        jp = build_jp_section({"7203": entry(), "1111": entry(include=False), "2222": entry(fy="2024-03-31"),
                               "3333": entry(annual=None), "4444": entry(fy=None, filed="2026-06-10")}, "2026-10-18")
        self.assertEqual(sorted(jp), ["4444", "7203"])

    def test_empty_values_are_omitted_and_raw_only_when_different(self):
        jp = build_jp_section({"7203": entry(), "9984": entry(annual=11.0, annualRaw=27.5, flag="split_mixed", interim=None)},
                              "2026-10-18")
        self.assertNotIn("annualRaw", jp["7203"])
        self.assertNotIn("flag", jp["7203"])
        self.assertEqual(jp["9984"]["annualRaw"], 27.5)
        self.assertEqual(jp["9984"]["flag"], "split_mixed")
        self.assertNotIn("interim", jp["9984"])


class ValidateTest(unittest.TestCase):
    def doc(self, jp=None, us=None):
        return build_document(jp if jp is not None else build_jp_section({"7203": entry()}, "2026-10-18"),
                              us or {}, "2026-10-18T13:30:00Z")

    def test_good_document_passes(self):
        self.assertEqual(validate(self.doc()), [])

    def test_bad_values_are_reported(self):
        jp = {"7203": {"fy": "2026-03-31", "annual": -1}, "BAD!": {"fy": "2026-03-31", "annual": 5},
              "9984": {"fy": "2026-03-31", "annual": 1000, "annualRaw": 5}, "1234": {"annual": 5}}
        problems = validate(self.doc(jp=jp))
        self.assertEqual(len(problems), 4)

    def test_drop_in_count_is_reported(self):
        prev = {"jp": 100, "us": 10}
        jp = {f"{1000 + i}": {"fy": "2026-03-31", "annual": 5} for i in range(90)}
        us = {f"T{i}": {"fy": "2026-03-31", "annual": 1} for i in range(10)}
        problems = validate(self.doc(jp=jp, us=us), prev)
        self.assertEqual(len(problems), 1)
        self.assertIn("jp", problems[0])
        self.assertEqual(validate(self.doc(jp={f"{1000 + i}": {"fy": "2026-03-31", "annual": 5} for i in range(96)}, us=us), prev), [])

    def test_wrong_schema(self):
        d = self.doc()
        d["schema"] = 2
        self.assertTrue(validate(d))


class PublishTest(unittest.TestCase):
    def run_publish(self, tmp, entries, prev_status=None):
        jp_dir = os.path.join(tmp, "jp")
        os.makedirs(jp_dir)
        with open(os.path.join(jp_dir, "entries.json"), "w", encoding="utf-8") as f:
            json.dump(entries, f)
        status = os.path.join(tmp, "status.json")
        if prev_status:
            with open(status, "w") as f:
                json.dump(prev_status, f)
        out = os.path.join(tmp, "public")
        code = publish.main(["--jp-dir", jp_dir, "--us-file", os.path.join(tmp, "none.json"),
                             "--status-file", status, "--out", out])
        return code, out, status

    def test_publishes_when_checks_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            today = publish.datetime.datetime.now(publish.datetime.timezone.utc).strftime("%Y-%m-%d")
            code, out, status = self.run_publish(tmp, {"7203": entry(fy=today, filed=today)})
            self.assertEqual(code, 0)
            with open(os.path.join(out, "dividends.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["jp"]["7203"]["annual"], 95)
            with open(status) as f:
                self.assertEqual(json.load(f)["jp"], 1)

    def test_writes_nothing_when_checks_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            today = publish.datetime.datetime.now(publish.datetime.timezone.utc).strftime("%Y-%m-%d")
            code, out, status = self.run_publish(tmp, {"7203": entry(fy=today, filed=today)}, prev_status={"jp": 100, "us": 0})
            self.assertEqual(code, 1)
            self.assertFalse(os.path.exists(out))
            with open(status) as f:
                self.assertEqual(json.load(f)["jp"], 100)  # 前回の記録は、そのまま


if __name__ == "__main__":
    unittest.main()
