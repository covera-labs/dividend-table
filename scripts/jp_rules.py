"""日本株の1株配当を、分割の影響をそろえたうえで決める。

考え方（試運転4・5の結果）:
- 発行済株式総数の「当期÷前期」で、分割があった年を見つける（308社中307社で判定できた）
- 剰余金の配当の表の「総額÷1株配当」（その配当を受けた株数）の比で、年の中で基準が混ざったかを見分ける
- 混ざったときは、最新の株数の基準に組み直す:
    年間の1株配当 ＝ 各回の1株配当 × （その回の株数 ÷ 最新の回の株数）の合計
  （＝ 各回の総額の合計 ÷ 最新の株数）
"""
from dataclasses import dataclass, field

ISSUED_LOW, ISSUED_HIGH = 0.7, 1.4    # 発行済株式数の当期÷前期が、この範囲なら分割なし
TABLE_OK = 1.25                        # 表の株数の比が、この以下なら「基準がそろっている」
TABLE_MAX = 20                         # これを超える比は、読み違いとみなす
SUM_TOL = 0.005                        # 表の合計と、報告書の年間配当の食い違いの許容（0.5%）


@dataclass
class Decision:
    include: bool                      # 配る表に載せるか
    flag: str | None = None            # 注意の印（None / split_year / split_mixed / unchecked / check）
    annual: float | None = None        # 最新の株数の基準にそろえた、年間の1株配当
    annual_raw: float | None = None    # 報告書の値のまま（annual と違うときだけ意味がある）
    interim: float | None = None
    payments: list = field(default_factory=list)  # [{"d": 決議日, "v": 1株配当, "k": "i"|"f"|"q"}]
    reason: str = ""                   # 載せない・印を付けた理由（ログ用）


def issued_ratio(ex):
    cur, prev = ex.issued.get("当期"), ex.issued.get("前期")
    return cur / prev if cur and prev else None


def _shares(row):
    return row.total / row.dps if row.dps and row.total and row.dps > 0 and row.total > 0 else None


def table_ratio(ex):
    """表の株数の比（大きい方÷小さい方）。2行以上で、表が読めるときだけ。出せなければ None"""
    if not ex.table_sane:
        return None
    s = [x for x in (_shares(r) for r in ex.table) if x]
    return max(s) / min(s) if len(s) >= 2 else None


def _kind(i, n, has_interim):
    if n >= 2 and i == 0 and has_interim:
        return "i"
    return "f" if i == n - 1 else "q"


def _payments(ex, scale=None):
    out = []
    if not ex.table_sane:
        return out
    rows = [r for r in ex.table if r.dps and r.dps > 0]
    for i, r in enumerate(rows):
        v = r.dps * (scale[i] if scale else 1.0)
        out.append({"d": r.date, "v": round(v, 4), "k": _kind(i, len(rows), ex.interim is not None)})
    return out


def decide(ex):
    annual = ex.annual
    if annual is None or annual <= 0:
        return Decision(False, reason="年間の1株配当が無い（無配、または記載なし）")
    if annual >= 100000:
        return Decision(False, reason="1株配当が大きすぎる（読み違い）")

    ir = issued_ratio(ex)
    tr = table_ratio(ex)
    plain = Decision(True, annual=annual, interim=ex.interim, payments=_payments(ex))

    # 発行済株式数が取れない
    if ir is None:
        if tr is not None and tr > TABLE_OK:
            return Decision(False, reason="発行済株式数が取れず、表の株数の比が大きい（分割の疑い）")
        plain.flag = "unchecked"
        plain.reason = "発行済株式数が取れず、分割を確かめられなかった"
        return plain

    # 分割なし
    if ISSUED_LOW <= ir <= ISSUED_HIGH:
        if tr is not None and TABLE_OK < tr <= TABLE_MAX:
            return Decision(False, flag="check", reason="発行済株式数は変わらないのに、表の株数の比が大きい")
        return plain

    # ここから、発行済株式数が大きく変わった年
    if tr is not None and tr <= TABLE_OK:
        plain.flag = "split_year"
        plain.reason = "分割の年。中間と期末の基準はそろっている"
        return plain

    if tr is not None and tr <= TABLE_MAX:
        return _rebuild(ex, annual)

    # 表で判定できない
    if not ex.interim or ex.interim <= 0:
        plain.flag = "split_year"
        plain.reason = "分割の年。配当が1回だけなので、基準は混ざらない"
        return plain
    return Decision(False, reason="分割の年で、中間があるのに、決議の表で基準を確かめられない")


def _rebuild(ex, annual_raw):
    """基準が混ざった年を、最新の株数の基準に組み直す"""
    rows = [r for r in ex.table if r.dps and r.dps > 0]
    if any(not (r.total and r.total > 0) for r in rows):
        return Decision(False, reason="総額が無い行があり、組み直せない")
    total_dps = sum(r.dps for r in rows)
    if abs(total_dps - annual_raw) > max(0.01, SUM_TOL * annual_raw):
        return Decision(False, reason="表の合計と年間の1株配当が合わず、組み直せない")
    shares = [r.total / r.dps for r in rows]
    last = shares[-1]
    scale = [s / last for s in shares]
    payments = _payments(ex, scale)
    adjusted = sum(p["v"] for p in payments)
    interim = next((p["v"] for p in payments if p["k"] == "i"), None)
    return Decision(True, flag="split_mixed", annual=round(adjusted, 4), annual_raw=annual_raw,
                    interim=interim, payments=payments, reason="分割の前後が混ざっていたので、最新の株数の基準に組み直した")
