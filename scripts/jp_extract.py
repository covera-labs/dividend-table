"""有価証券報告書のCSV（XBRLを表にしたもの）から、配当まわりの数字を取り出す。

CSVは、タブ区切り・UTF-16・改行CRLF。列は次の9つ:
  0 要素ID / 1 項目名 / 2 コンテキストID / 3 相対年度 / 4 連結・個別 / 5 期間・時点 / 6 ユニットID / 7 単位 / 8 値
"""
import csv
import io
import os
import re
import zipfile
from dataclasses import dataclass, field

ANNUAL = "DividendPaidPerShareSummaryOfBusinessResults"
INTERIM = "InterimDividendPaidPerShareSummaryOfBusinessResults"
TABLE_DPS = "DividendPerShareDividendsOfSurplus"
TABLE_TOTAL = "TotalAmountOfDividendsDividendsOfSurplus"
TABLE_DATE = "DateOfResolutionDividendsOfSurplus"
ISSUED = "TotalNumberOfIssuedSharesSummaryOfBusinessResults"

DPS_LIMIT = 100000  # 1株配当がこれ以上なら、読み違いとみなす


@dataclass
class TableRow:
    date: str
    dps: float | None
    total: float | None


@dataclass
class Extract:
    annual: float | None = None  # 当期の年間の1株配当（報告書のまま）
    interim: float | None = None  # 当期の中間
    table: list = field(default_factory=list)  # 剰余金の配当の表（日付順）
    table_sane: bool = True  # 同じ日付の重複や、極端な値が無いか
    issued: dict = field(default_factory=dict)  # 相対年度 → 発行済株式総数


def num(s):
    """「82.00」「1,234」を数にする。「－」や空は None"""
    try:
        return float(s.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def norm_date(s):
    """「2025-11-05」「2025年11月5日」を、ISOの形にする。読めなければそのまま"""
    s = (s or "").strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s) or re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    return f"{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}" if m else s


def norm_rel(label):
    """「当期末」→「当期」のように、相対年度の言い方をそろえる"""
    return label.replace("期末", "期").replace("末", "").strip()


def read_rows(zip_bytes):
    """ZIPの中の提出本文のCSV（XBRL_TO_CSV の下。監査報告書は除く）を全部読む"""
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = [n for n in z.namelist()
             if n.lower().endswith(".csv") and "XBRL_TO_CSV" in n and not os.path.basename(n).startswith("jpaud")]
    rows = []
    for n in names:
        raw = z.read(n)
        try:
            text = raw.decode("utf-16")
        except UnicodeDecodeError:
            text = raw.decode("utf-16-le", errors="replace")
        for r in csv.reader(io.StringIO(text.lstrip("﻿")), delimiter="\t", quotechar='"'):
            if len(r) >= 9:
                rows.append(r)
    return rows


def _pick(cands):
    """個別（提出会社）の印が付いた行を優先して、最初の数字を返す"""
    cands = [(c, v) for c, v in cands if v is not None]
    for ctx, v in cands:
        if "NonConsolidated" in ctx:
            return v
    return cands[0][1] if cands else None


def extract(rows):
    annual, interim, issued_all = [], [], {}
    table = {}
    for r in rows:
        elem = r[0].split(":")[-1]
        ctx, rel = r[2], r[3]
        if elem == ANNUAL and rel == "当期":
            annual.append((ctx, num(r[8])))
        elif elem == INTERIM and rel == "当期":
            interim.append((ctx, num(r[8])))
        elif "FilingDateInstant_Row" in ctx and elem in (TABLE_DPS, TABLE_TOTAL, TABLE_DATE):
            t = table.setdefault(ctx, {})
            if elem == TABLE_DATE:
                t["date"] = norm_date(r[8])
            elif elem == TABLE_DPS:
                t["dps"] = num(r[8])
            else:
                t["total"] = num(r[8])
        elif elem == ISSUED:
            v = num(r[8])
            if v is not None and v > 0:
                key = norm_rel(rel)
                if key not in issued_all or "NonConsolidated" in ctx:
                    issued_all[key] = v
    rows_sorted = sorted((TableRow(t.get("date", ""), t.get("dps"), t.get("total")) for t in table.values()),
                         key=lambda x: x.date)
    dates = [x.date for x in rows_sorted]
    sane = len(dates) == len(set(dates)) and all((x.dps or 0) < DPS_LIMIT for x in rows_sorted)
    return Extract(annual=_pick(annual), interim=_pick(interim), table=rows_sorted, table_sane=sane,
                   issued=issued_all)
