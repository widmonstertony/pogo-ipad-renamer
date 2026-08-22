from __future__ import annotations

import unittest

from pogo_iphone_renamer.protocol import parse_http_payload


class ProtocolTests(unittest.TestCase):
    def test_json_response(self) -> None:
        value = parse_http_payload("application/json", b'{"jsonrpc":"2.0","id":1,"result":{}}')
        self.assertEqual(value["id"], 1)

    def test_sse_response(self) -> None:
        body = b'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"ok":true}}\n\n'
        value = parse_http_payload("text/event-stream", body)
        self.assertEqual(value["result"], {"ok": True})

    def test_empty_notification_response(self) -> None:
        self.assertIsNone(parse_http_payload("application/json", b""))


if __name__ == "__main__":
    unittest.main()

