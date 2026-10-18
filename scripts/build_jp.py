"""日本株の表を作る（毎日の更新と、最初の過去分の取り込みに、同じ部品を使う）。

流れ:
  1. 指定した期間の平日について、書類一覧を取る（取り込み済みの日は飛ばす。最近の数日は、毎回取り直す）
  2. 通常の会社の有価証券報告書（120）と訂正（130）のうち、まだ処理していないものを、古い順に処理する
  3. 1件ずつ、CSVを取り、配当を取り出し、分割の規則で「載せるか・いくらか」を決めて、会社ごとの状態に入れる
  4. 状態を `data/jp/` に保存する（途中で止まっても、続きから動かせる）

状態の持ち方（`data/jp/` の中）:
  entries.json   会社（4桁コード）→ 最新の報告書の結果。載せない会社も、理由つきで残す
  processed.json 処理した書類の docID（同じ書類を2度取らないため）
  days.json      すべて処理し終えた日

キーとURLは、ログに出さない。
"""
import argparse
import datetime
import json
import os
import sys
from collections import Counter

from scripts.edinet import Client, EdinetError, code4, is_target
from scripts.jp_extract import extract, read_rows
from scripts.jp_rules import decide, issued_ratio, table_ratio


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


def weekdays(start, end):
    d, e = datetime.date.fromisoformat(start), datetime.date.fromisoformat(end)
    while d <= e:
        if d.weekday() < 5:
            yield d.isoformat()
        d += datetime.timedelta(days=1)


def note(title, msg):
    """実行の画面の「注記」に出す。改行は %0A、% は %25 にする"""
    msg = msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::notice title={title}::{msg}")


def entry_from(doc, decision, ex):
    return {
        "docId": doc["docID"],
        "docType": doc.get("docTypeCode"),
        "filed": (doc.get("submitDateTime") or "")[:10],
        "submitted": doc.get("submitDateTime") or "",
        "fy": doc.get("periodEnd"),
        "name": (doc.get("filerName") or "")[:40],
        "include": decision.include,
        "flag": decision.flag,
        "reason": decision.reason,
        "annual": decision.annual,
        "annualRaw": decision.annual_raw,
        "interim": decision.interim,
        "payments": decision.payments,
        "issuedRatio": round(issued_ratio(ex), 4) if issued_ratio(ex) else None,
        "tableRatio": round(table_ratio(ex), 4) if table_ratio(ex) else None,
    }


def is_newer(doc, current):
    """今ある結果より、新しい書類か（提出日時で比べる。同じなら訂正を優先）"""
    if current is None:
        return True
    a, b = doc.get("submitDateTime") or "", current.get("submitted") or ""
    if a != b:
        return a > b
    return doc.get("docTypeCode") == "130"


def run(client, data_dir, start, end, max_docs=600, rescan_days=3, today=None, max_consecutive_failures=4):
    p = lambda name: os.path.join(data_dir, name)
    entries = load_json(p("entries.json"), {})
    processed = set(load_json(p("processed.json"), []))
    done_days = set(load_json(p("days.json"), []))
    today = today or datetime.date.today().isoformat()
    recent = {(datetime.date.fromisoformat(today) - datetime.timedelta(days=i)).isoformat() for i in range(rescan_days + 1)}

    stats = Counter()
    consecutive = 0
    stopped = None
    try:
        for day in weekdays(start, end):
            if day in done_days and day not in recent:
                stats["days_skipped"] += 1
                continue
            docs = sorted((d for d in client.list_documents(day) if is_target(d)),
                          key=lambda d: d.get("submitDateTime") or "")
            stats["days_listed"] += 1
            all_done = True
            for doc in docs:
                if doc["docID"] in processed:
                    continue
                if stats["fetched"] >= max_docs:
                    all_done = False
                    stopped = "上限の件数に達した"
                    break
                key = code4(doc["secCode"])
                if not is_newer(doc, entries.get(key)):  # もっと新しい報告書の結果が、すでにある
                    stats["older_skipped"] += 1
                    processed.add(doc["docID"])
                    continue
                try:
                    zb = client.fetch_csv_zip(doc["docID"])
                    consecutive = 0
                except EdinetError as e:
                    stats["failed"] += 1
                    consecutive += 1
                    all_done = False
                    print(f"取得に失敗: {key} {e}")
                    if consecutive >= max_consecutive_failures:
                        stopped = f"続けて{consecutive}回失敗したので止めた"
                        break
                    continue
                stats["fetched"] += 1
                processed.add(doc["docID"])
                if zb is None:
                    stats["no_csv"] += 1
                    if key not in entries:
                        entries[key] = {"docId": doc["docID"], "docType": doc.get("docTypeCode"),
                                        "filed": (doc.get("submitDateTime") or "")[:10],
                                        "submitted": doc.get("submitDateTime") or "", "fy": doc.get("periodEnd"),
                                        "name": (doc.get("filerName") or "")[:40], "include": False, "flag": None,
                                        "reason": "CSVが取れなかった", "annual": None, "annualRaw": None,
                                        "interim": None, "payments": [], "issuedRatio": None, "tableRatio": None}
                    continue
                ex = extract(read_rows(zb))
                entries[key] = entry_from(doc, decide(ex), ex)
                stats["included" if entries[key]["include"] else "excluded"] += 1
            if stopped:
                break
            if all_done:
                done_days.add(day)
    finally:
        save_json(p("entries.json"), entries)
        save_json(p("processed.json"), sorted(processed))
        save_json(p("days.json"), sorted(done_days))
    return stats, stopped, entries


def summarize(entries):
    inc = [e for e in entries.values() if e["include"]]
    flags = Counter(e["flag"] or "なし" for e in inc)
    reasons = Counter(e["reason"] for e in entries.values() if not e["include"])
    lines = [f"会社数 {len(entries)} / 載せる {len(inc)} / 載せない {len(entries) - len(inc)}",
             "載せるものの印: " + ", ".join(f"{k} {v}" for k, v in flags.most_common()),
             "載せない理由: " + " / ".join(f"{k} {v}" for k, v in reasons.most_common(6))]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="日本株の表を作る")
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--max-docs", type=int, default=600)
    ap.add_argument("--rescan-days", type=int, default=3)
    ap.add_argument("--data-dir", default="data/jp")
    ap.add_argument("--sleep", type=float, default=1.5)
    a = ap.parse_args(argv)
    try:
        client = Client(os.environ.get("EDINET_API_KEY", ""), sleep=a.sleep)
    except EdinetError as e:
        print(f"::error title=EDINET::{e}")
        return 1
    try:
        stats, stopped, entries = run(client, a.data_dir, a.start, a.end, a.max_docs, a.rescan_days)
    except EdinetError as e:
        print(f"::error title=EDINET::{e}")
        return 1
    note("日本株の取り込み", f"{a.start}〜{a.end}\n取った書類 {stats['fetched']}件 / 失敗 {stats['failed']}件 / "
         f"一覧を取った日 {stats['days_listed']} / 飛ばした日 {stats['days_skipped']}\n"
         f"新しく載せる {stats['included']}件 / 載せない {stats['excluded']}件 / 古い書類で飛ばした {stats['older_skipped']}件"
         + (f"\n止めた理由: {stopped}" if stopped else ""))
    note("日本株の集計", summarize(entries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
