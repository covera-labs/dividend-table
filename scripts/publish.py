"""日本株・米国株の状態から、配る表を作って、検査に通れば `public/` に書き出す。

通らなかったときは、何も書き出さずに失敗で終わる（前回公開した表が、そのまま残る）。
"""
import argparse
import datetime
import json
import os
import sys

from scripts.build_jp import load_json, note, save_json
from scripts.table import build_document, build_jp_section, make_status, validate, write_public


def main(argv=None):
    ap = argparse.ArgumentParser(description="配る表を作って検査する")
    ap.add_argument("--jp-dir", default="data/jp")
    ap.add_argument("--us-file", default="data/us.json")
    ap.add_argument("--status-file", default="data/status.json")
    ap.add_argument("--out", default="public")
    a = ap.parse_args(argv)

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today = now[:10]
    jp = build_jp_section(load_json(os.path.join(a.jp_dir, "entries.json"), {}), today)
    us = load_json(a.us_file, {})
    doc = build_document(jp, us, now)
    prev = load_json(a.status_file, None)
    problems = validate(doc, prev)
    status = make_status(doc)
    if problems:
        note("公開の検査に落ちた", "\n".join(problems[:20]) + (f"\nほか{len(problems) - 20}件" if len(problems) > 20 else ""))
        print("::error title=公開の検査::公開しませんでした（前回の表のまま）")
        return 1
    write_public(a.out, doc)
    save_json(a.status_file, status)
    size = os.path.getsize(os.path.join(a.out, "dividends.json"))
    note("配る表", f"日本株 {status['jp']}社 / 米国株 {status['us']}社 / {size / 1024:.0f}KB\n印: {json.dumps(status['flags'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
