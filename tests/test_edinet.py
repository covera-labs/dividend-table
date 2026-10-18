"""EDINETクライアントの、キーを漏らさない作りと、再試行を、手元の偽サーバーで確かめる。"""
import http.server
import io
import json
import threading
import unittest
import zipfile

from scripts.edinet import Client, EdinetError, clean_key, code4, is_target


class Server:
    def __init__(self, handler):
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def handler(script):
    state = {"n": 0, "keys": []}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            state["n"] += 1
            state["keys"].append(self.path)
            code, body = script(state["n"], self.path)
            self.send_response(code)
            self.end_headers()
            self.wfile.write(body)
    return H, state


class ClientTest(unittest.TestCase):
    def test_clean_key_removes_all_whitespace(self):
        self.assertEqual(clean_key(" abc\r\n\r\n"), "abc")
        self.assertEqual(clean_key(None), "")

    def test_empty_key_is_rejected(self):
        with self.assertRaises(EdinetError):
            Client(" \n")

    def test_list_documents_and_key_is_trimmed_in_url(self):
        H, st = handler(lambda n, p: (200, json.dumps({"results": [{"docID": "S1"}]}).encode()))
        s = Server(H)
        try:
            docs = Client("secretkey\r\n", base=s.base, sleep=0, backoff=0).list_documents("2026-06-26")
        finally:
            s.close()
        self.assertEqual(docs, [{"docID": "S1"}])
        self.assertIn("Subscription-Key=secretkey", st["keys"][0])
        self.assertNotIn("%0D", st["keys"][0])

    def test_retries_on_500_then_succeeds(self):
        H, st = handler(lambda n, p: (500, b"") if n < 3 else (200, b'{"results": []}'))
        s = Server(H)
        try:
            self.assertEqual(Client("k", base=s.base, sleep=0, backoff=0).list_documents("2026-06-26"), [])
        finally:
            s.close()
        self.assertEqual(st["n"], 3)

    def test_error_message_has_no_url_and_no_key(self):
        H, st = handler(lambda n, p: (403, b""))
        s = Server(H)
        try:
            with self.assertRaises(EdinetError) as cm:
                Client("topsecretkey", base=s.base, sleep=0, backoff=0).list_documents("2026-06-26")
        finally:
            s.close()
        msg = str(cm.exception)
        self.assertNotIn("topsecretkey", msg)
        self.assertNotIn("127.0.0.1", msg)
        self.assertEqual(st["n"], 1)  # 403 は再試行しない

    def test_fetch_csv_zip_returns_none_for_non_zip(self):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("a.txt", "x")
        H, st = handler(lambda n, p: (200, b'{"metadata":{"status":"404"}}') if "S0" in p else (200, b.getvalue()))
        s = Server(H)
        try:
            c = Client("k", base=s.base, sleep=0, backoff=0)
            self.assertIsNone(c.fetch_csv_zip("S0"))
            self.assertIsNotNone(c.fetch_csv_zip("S1"))
        finally:
            s.close()


class FilterTest(unittest.TestCase):
    def test_is_target(self):
        ok = {"docTypeCode": "120", "ordinanceCode": "010", "secCode": "72030", "csvFlag": "1", "withdrawalStatus": "0"}
        self.assertTrue(is_target(ok))
        self.assertTrue(is_target({**ok, "docTypeCode": "130"}))
        self.assertFalse(is_target({**ok, "docTypeCode": "140"}))
        self.assertFalse(is_target({**ok, "ordinanceCode": "030"}))
        self.assertFalse(is_target({**ok, "secCode": None}))
        self.assertFalse(is_target({**ok, "csvFlag": "0"}))
        self.assertFalse(is_target({**ok, "withdrawalStatus": "1"}))

    def test_code4(self):
        self.assertEqual(code4("72030"), "7203")
        self.assertEqual(code4("297A0"), "297A")


if __name__ == "__main__":
    unittest.main()
