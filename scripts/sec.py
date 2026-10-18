"""SEC EDGAR（data.sec.gov）のクライアント（標準ライブラリだけ）。

- 連絡先を User-Agent で名乗る（SECの公平アクセスの決まり）。連絡先はログに出さない
- 1秒に10回までの決まりに対して、通信の間を0.5秒以上空ける（1回の更新で、通信は数回だけ）
- 429と5xxは、間を空けて、最大3回まで試す。403は、名乗りの不足などなので、試さない
"""
import json
import time
import urllib.error
import urllib.request

FRAMES = "https://data.sec.gov/api/xbrl/frames/us-gaap"
TICKERS = "https://www.sec.gov/files/company_tickers.json"


class SecError(Exception):
    """通信の失敗。メッセージに連絡先を入れない。"""


def clean_contact(raw):
    """Secretに改行が付いていても動くように、改行だけを取る（連絡先は、空白を含む）"""
    return (raw or "").replace("\r", "").replace("\n", "").strip()


class Client:
    def __init__(self, contact, sleep=0.5, backoff=5, frames_url=FRAMES, tickers_url=TICKERS):
        self._ua = clean_contact(contact)
        if not self._ua:
            raise SecError("連絡先（SEC_CONTACT）が空です")
        self._sleep, self._backoff = sleep, backoff
        self._frames, self._tickers = frames_url, tickers_url
        self._last = 0.0

    def _get_json(self, url):
        last = None
        for attempt in range(3):
            gap = self._sleep - (time.monotonic() - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.monotonic()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": self._ua, "Accept-Encoding": "identity"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                if e.code == 429 or e.code >= 500:
                    time.sleep(self._backoff * (attempt + 1))
                    continue
                break
            except ValueError as e:
                last = "JSONとして読めない"
                break
            except Exception as e:
                last = type(e).__name__
                time.sleep(self._backoff * 0.4)
        raise SecError(f"SECの取得に失敗しました（{last}）")

    def frame(self, tag, year):
        """期間ごとに全社分を返すAPI。通期（CY2025 など）の、1株あたりのドルの値"""
        data = self._get_json(f"{self._frames}/{tag}/USD-per-shares/CY{year}.json")
        return data.get("data", [])

    def tickers(self):
        """CIK → ティッカーの一覧（1社に複数あることがある）"""
        out = {}
        for item in self._get_json(self._tickers).values():
            out.setdefault(int(item["cik_str"]), []).append(str(item["ticker"]).upper())
        return out
