"""米国株の表を作る（通信は数回だけ。毎回、全部作り直す）。

  Frames API: 最新の年と、1つ前の年 × 2項目 ＝ 4回、ティッカーの一覧 1回
失敗したときは、何も書かずに終わる（前回の `data/us.json` が残る）。
"""
import argparse
import datetime
import os
import sys
from collections import Counter

from scripts.build_jp import load_json, note, save_json
from scripts.sec import Client, SecError
from scripts.us_rules import DECLARED, PAID, apply_overrides, build_section, find_orphans


def run(client, today, out_path):
    year = int(today[:4])
    years = [year, year - 1]
    declared = [client.frame(DECLARED, y) for y in years]
    paid = [client.frame(PAID, y) for y in years]
    tickers = client.tickers()
    overrides = load_json(os.path.join(os.path.dirname(out_path) or ".", "us_overrides.json"), {})
    section = build_section(declared, paid, tickers, today, overrides)
    # 再編でティッカーが付け替わった会社の候補（配当の値はあるのに、ティッカーが付いていない会社）
    orphans = find_orphans(declared, paid, apply_overrides(tickers, overrides))
    previous = load_json(out_path, {})
    if previous and len(section) < len(previous) * 0.9:
        raise SecError(f"件数が前回の90%を下回った（{len(previous)} → {len(section)}）。書き込まなかった")
    save_json(out_path, section)
    return section, orphans


def main(argv=None):
    ap = argparse.ArgumentParser(description="米国株の表を作る")
    ap.add_argument("--out", default="data/us.json")
    a = ap.parse_args(argv)
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    try:
        client = Client(os.environ.get("SEC_CONTACT", ""))
        section, orphans = run(client, today, a.out)
    except SecError as e:
        print(f"::error title=SEC::{e}")
        return 1
    flags = Counter(v.get("flag") or "なし" for v in section.values())
    tags = Counter(v["tag"] for v in section.values())
    note("米国株の更新", f"{len(section)}銘柄\n項目: " + ", ".join(f"{k} {v}" for k, v in tags.most_common())
         + "\n印: " + ", ".join(f"{k} {v}" for k, v in flags.most_common())
         + f"\nティッカーが付いていない会社（再編の候補）: {len(orphans)}社" + (f"\n" + " / ".join(n for _, n in orphans[:10]) if orphans else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
