"""配る表（dividends.json）を組み立てて、公開の前の検査をする。

表の形は、設計案のとおり。値が無い欄（印や中間など）は、載せない（アプリは「無い」＝「なし」と扱う）。
"""
import datetime
import json
import os
import re

SCHEMA = 1
MAX_AGE_DAYS = 460          # 決算期末から、これより古い会社は載せない（約15か月）
JP_KEEP, US_KEEP = 0.95, 0.90   # 前回の件数に対して、最低でもこの割合は残っていること
CODE_JP = re.compile(r"^[0-9][0-9A-Z]{3}$")
CODE_US = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def _days_old(date_str, today):
    try:
        return (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(date_str[:10])).days
    except (ValueError, TypeError):
        return None


def build_jp_section(entries, today):
    out = {}
    for code, e in sorted(entries.items()):
        if not e.get("include") or e.get("annual") is None:
            continue
        age = _days_old(e.get("fy") or e.get("filed"), today)
        if age is None or age > MAX_AGE_DAYS:
            continue
        item = {"fy": e.get("fy"), "filed": e.get("filed"), "annual": e["annual"]}
        raw = e.get("annualRaw")
        if raw is not None and raw != e["annual"]:
            item["annualRaw"] = raw
        if e.get("interim") is not None:
            item["interim"] = e["interim"]
        if e.get("payments"):
            item["payments"] = e["payments"]
        if e.get("flag"):
            item["flag"] = e["flag"]
        out[code] = item
    return out


def build_document(jp, us, now):
    return {"schema": SCHEMA, "generatedAt": now,
            "sources": {"jp": "EDINET（金融庁）", "us": "SEC EDGAR"},
            "jp": jp, "us": us}


def make_status(doc):
    flags = {}
    for section in ("jp", "us"):
        for v in doc[section].values():
            if v.get("flag"):
                flags[v["flag"]] = flags.get(v["flag"], 0) + 1
    return {"schema": SCHEMA, "generatedAt": doc["generatedAt"], "jp": len(doc["jp"]), "us": len(doc["us"]),
            "flags": flags}


def validate(doc, prev_status=None):
    """公開してよいかの検査。問題の一覧を返す（空なら、公開してよい）"""
    problems = []
    if doc.get("schema") != SCHEMA:
        problems.append("schemaの版が違う")
    for section, pattern in (("jp", CODE_JP), ("us", CODE_US)):
        for code, v in doc.get(section, {}).items():
            if not pattern.match(code):
                problems.append(f"{section}: コードの形がおかしい: {code}")
                continue
            a = v.get("annual")
            if not isinstance(a, (int, float)) or not (0 < a < 100000):
                problems.append(f"{section}: {code} の annual が範囲外: {a}")
            raw = v.get("annualRaw")
            if raw and a and not (1 / 50 <= a / raw <= 50):
                problems.append(f"{section}: {code} の annual と annualRaw の差が大きすぎる")
            if not v.get("fy"):
                problems.append(f"{section}: {code} に fy が無い")
    if prev_status:
        for section, keep in (("jp", JP_KEEP), ("us", US_KEEP)):
            before, now = prev_status.get(section, 0), len(doc.get(section, {}))
            if before and now < before * keep:
                problems.append(f"{section}: 件数が前回の{keep:.0%}を下回った（{before} → {now}）")
    return problems


def write_public(out_dir, doc):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "dividends.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    with open(os.path.join(out_dir, "status.json"), "w", encoding="utf-8") as f:
        json.dump(make_status(doc), f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
