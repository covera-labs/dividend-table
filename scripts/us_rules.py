"""米国株の1株配当を、会社ごとに決める。

使う項目（Frames API、通期の1株あたりのドル）:
  宣言した額  CommonStockDividendsPerShareDeclared
  実際に払った額  CommonStockDividendsPerShareCashPaid

試運転（2026/10/18）で、2つの項目で約1,500社が取れ、両方にある174社のうち45社で値が違うと分かった。
違いの多くは、四半期や月ごとの配当を、通期の欄に入れている会社だった。そこで、次の順に決める:
  1. 最新の決算期（期末が新しい）の値を使う。期の長さが、340〜390日のものだけ
  2. 2つの項目が同じ決算期にあり、値の差が5%以内なら、「宣言した額」を使う
  3. 差が5%を超え、大きい値が小さい値の整数倍（2・3・4・6・12倍。誤差5%）なら、大きい値を使う
     （小さい値は、四半期や月の配当を、通期として入れたものとみなす）。印は freq_fixed
  4. それ以外の食い違いは、「宣言した額」を使い、印を conflict にする
"""
import datetime

DECLARED, PAID = "CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"
TOLERANCE = 0.05
MAX_AGE_DAYS = 460
MIN_DAYS, MAX_DAYS = 340, 390
MAX_VALUE = 5000
FREQUENCIES = (2, 3, 4, 6, 12)


def _days(a, b):
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


def latest_per_cik(rows_by_year):
    """複数の年のFramesから、会社（CIK）ごとに、期末がいちばん新しい通期の値を選ぶ"""
    best = {}
    for rows in rows_by_year:
        for r in rows:
            try:
                length = _days(r["start"], r["end"])
                val = float(r["val"])
            except (KeyError, ValueError, TypeError):
                continue
            if not (MIN_DAYS <= length <= MAX_DAYS) or not (0 < val < MAX_VALUE):
                continue
            cik = int(r["cik"])
            if cik not in best or r["end"] > best[cik]["end"]:
                best[cik] = {"end": r["end"], "val": val, "name": r.get("entityName", "")}
    return best


def _near_multiple(big, small):
    ratio = big / small
    return any(abs(ratio - k) <= TOLERANCE * k for k in FREQUENCIES)


def choose(declared, paid):
    """宣言した額と、実際に払った額（どちらも、{end, val} か None）から、(値, 期末, 項目, 印) を返す"""
    if declared and paid:
        gap = abs(_days(declared["end"], paid["end"]))
        if gap > 60:   # 別の決算期。新しいほうを使う
            pick, tag = (declared, "declared") if declared["end"] >= paid["end"] else (paid, "paid")
            return pick["val"], pick["end"], tag, None
        d, p = declared["val"], paid["val"]
        if abs(d - p) <= TOLERANCE * max(d, p):
            return d, declared["end"], "declared", None
        big, small = max(d, p), min(d, p)
        if _near_multiple(big, small):
            tag = "declared" if d >= p else "paid"
            end = declared["end"] if tag == "declared" else paid["end"]
            return big, end, tag, "freq_fixed"
        return d, declared["end"], "declared", "conflict"
    if declared:
        return declared["val"], declared["end"], "declared", None
    if paid:
        return paid["val"], paid["end"], "paid", None
    return None


def apply_overrides(tickers, overrides):
    """会社の再編などで、ティッカーが別のCIKに付け替わったときの、手で決めた対応（ティッカー → 配当の記録があるCIK）"""
    merged = {cik: list(symbols) for cik, symbols in tickers.items()}
    for symbol, cik in (overrides or {}).items():
        for symbols in merged.values():
            if symbol in symbols:
                symbols.remove(symbol)
        merged.setdefault(int(cik), []).append(symbol)
    return merged


def find_orphans(declared_by_year, paid_by_year, tickers):
    """配当の値はあるのに、ティッカーが付いていない会社（CIK）。再編で、記録が古いCIKに残っている会社の候補"""
    best = {**latest_per_cik(paid_by_year), **latest_per_cik(declared_by_year)}
    return sorted(((cik, v["name"]) for cik, v in best.items() if cik not in tickers), key=lambda x: x[1])


def build_section(declared_by_year, paid_by_year, tickers, today, overrides=None):
    """ティッカー → {fy, annual, ccy, tag, flag} の表を作る"""
    declared, paid = latest_per_cik(declared_by_year), latest_per_cik(paid_by_year)
    tickers = apply_overrides(tickers, overrides)
    out = {}
    for cik in sorted(set(declared) | set(paid)):
        symbols = tickers.get(cik)
        if not symbols:
            continue
        picked = choose(declared.get(cik), paid.get(cik))
        if picked is None:
            continue
        value, end, tag, flag = picked
        if (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(end)).days > MAX_AGE_DAYS:
            continue
        entry = {"fy": end, "annual": round(value, 4), "ccy": "USD", "tag": tag}
        if flag:
            entry["flag"] = flag
        for s in symbols:
            out.setdefault(s, entry)
    return out
