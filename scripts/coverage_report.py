"""取り込んだ日本株が、上場している普通株のうち、どれだけを覆っているかを調べる。

EDINETの公式の「EDINETコードリスト」（Edinetcode.zip の中の CSV。Shift_JIS）の上場会社と、
`data/jp/entries.json` を突き合わせる。取れなかった会社が、どんな理由かを数える。

使い方:
  python3 -m scripts.coverage_report --codes Edinetcode.zip
"""
import argparse
import csv
import io
import json
import zipfile
from collections import Counter

REIT_WORDS = ("投資法人", "インフラファンド")


def read_listed(path):
    """上場している内国法人のうち、証券コードがあり、REIT・インフラファンドでないもの → {4桁コード: {name, closing, industry}}"""
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            raw = z.read(z.namelist()[0])
    else:
        with open(path, "rb") as f:
            raw = f.read()
    try:
        text = raw.decode("cp932")       # 公式のファイルは Shift_JIS
    except UnicodeDecodeError:
        text = raw.decode("utf-8-sig")
    lines = text.splitlines()
    start = 1 if lines and lines[0].startswith("ダウンロード実行日") else 0
    rows = list(csv.reader(io.StringIO("\n".join(lines[start:]))))
    idx = {h: i for i, h in enumerate(rows[0])}
    out = {}
    for r in rows[1:]:
        if len(r) < len(idx) or r[idx["上場区分"]] != "上場" or not r[idx["証券コード"]].strip():
            continue
        if r[idx["提出者種別"]] != "内国法人・組合":
            continue
        name = r[idx["提出者名"]]
        if any(w in name for w in REIT_WORDS):
            continue
        out[r[idx["証券コード"]].strip()[:4]] = {"name": name, "closing": r[idx["決算日"]], "industry": r[idx["提出者業種"]]}
    return out


def report(listed, entries):
    included = [c for c in listed if entries.get(c, {}).get("include")]
    excluded = {c: entries[c]["reason"] for c in listed if c in entries and not entries[c].get("include")}
    missing = [c for c in listed if c not in entries]
    by_reason = Counter(excluded.values())
    missing_by_close = Counter(listed[c]["closing"] for c in missing)
    return {"listed": len(listed), "included": len(included), "excluded": len(excluded), "missing": len(missing),
            "by_reason": by_reason, "missing_by_closing": missing_by_close, "missing_codes": sorted(missing)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="取り込みの網羅率を調べる")
    ap.add_argument("--codes", required=True, help="EDINETコードリスト（Edinetcode.zip、または、中のCSV）")
    ap.add_argument("--entries", default="data/jp/entries.json")
    a = ap.parse_args(argv)
    with open(a.entries, encoding="utf-8") as f:
        entries = json.load(f)
    listed = read_listed(a.codes)
    r = report(listed, entries)
    n = r["listed"]
    print(f"上場している普通株（REIT等を除く）: {n}社")
    print(f"  表に載る: {r['included']}社（{r['included'] / n:.1%}）")
    print(f"  取り込んだが載せない: {r['excluded']}社（{r['excluded'] / n:.1%}）")
    for reason, count in r["by_reason"].most_common():
        print(f"    {count}社: {reason}")
    print(f"  取り込めていない: {r['missing']}社（{r['missing'] / n:.1%}）")
    print("    決算月ごと（取り込めていない会社）: " + ", ".join(f"{k} {v}" for k, v in sorted(r["missing_by_closing"].items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
