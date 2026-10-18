"""EDINET API v2 のクライアント（標準ライブラリだけ）。

- キー入りのURLは、ログにも例外にも出さない（公開リポジトリのログは誰でも読めるため）
- 通信の間は、既定で1.5秒空ける（EDINETの「短時間の大量アクセス」の禁止を避けるため）
- 429（多すぎる）と5xxは、間を空けて、最大3回まで試す
"""
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

BASE = "https://api.edinet-fsa.go.jp/api/v2"


class EdinetError(Exception):
    """通信の失敗。メッセージにURLもキーも入れない。"""


def clean_key(raw):
    """Secretに改行・空白が付いていても動くように、空白をすべて取る"""
    return "".join((raw or "").split())


class Client:
    def __init__(self, key, base=BASE, sleep=1.5, backoff=5, user_agent="covera-labs-dividend-table"):
        self._key = clean_key(key)
        if not self._key:
            raise EdinetError("APIキーが空です")
        self._base = base
        self._sleep = sleep
        self._backoff = backoff
        self._ua = user_agent
        self._last = 0.0

    def _wait(self):
        gap = self._sleep - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()

    def _get(self, path, **params):
        params["Subscription-Key"] = self._key
        url = f"{self._base}/{path}?{urllib.parse.urlencode(params)}"
        last = None
        for attempt in range(3):
            self._wait()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": self._ua})
                with urllib.request.urlopen(req, timeout=90) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                if e.code == 429 or e.code >= 500:
                    time.sleep(self._backoff * (attempt + 1))
                    continue
                break
            except Exception as e:  # 通信の失敗。URLは出さない
                last = type(e).__name__
                time.sleep(self._backoff * 0.4)
        raise EdinetError(f"{path.split('/')[0]} の取得に失敗しました（{last}）")

    def list_documents(self, day):
        """その日に出た書類の一覧（day は YYYY-MM-DD）"""
        body = self._get("documents.json", date=day, type="2")
        try:
            return json.loads(body).get("results", [])
        except ValueError as e:
            raise EdinetError("書類一覧がJSONとして読めませんでした") from e

    def fetch_csv_zip(self, doc_id):
        """書類のXBRLを表にしたCSV（ZIP）。取れないときは None"""
        body = self._get(f"documents/{doc_id}", type="5")
        return body if zipfile.is_zipfile(io.BytesIO(body)) else None


def is_target(doc):
    """通常の会社の有価証券報告書（120）と訂正（130）で、CSVがあり、取下げでないもの"""
    return (doc.get("docTypeCode") in ("120", "130")
            and doc.get("ordinanceCode") == "010"
            and bool(doc.get("secCode"))
            and doc.get("csvFlag") == "1"
            and doc.get("withdrawalStatus") == "0")


def code4(sec_code):
    """EDINETの証券コード（5桁。末尾に0が付く）から、4桁のコードにする"""
    return sec_code[:4]
