import unittest

from backend.automation import session as browser_session
from backend.automation.socks_http_bridge import Socks5Bridge, _parse_socks5, _split_hostport


class Socks5ParseTests(unittest.TestCase):
    def test_parse_with_auth(self):
        host, port, user, pw = _parse_socks5("socks5://user:pass@1.2.3.4:7890")
        self.assertEqual((host, port, user, pw), ("1.2.3.4", 7890, "user", "pass"))

    def test_parse_without_auth(self):
        host, port, user, pw = _parse_socks5("socks5://5.6.7.8:1080")
        self.assertEqual((host, port, user, pw), ("5.6.7.8", 1080, None, None))

    def test_parse_default_port(self):
        host, port, _, _ = _parse_socks5("socks5://1.2.3.4")
        self.assertEqual((host, port), ("1.2.3.4", 1080))

    def test_parse_bare_host(self):
        host, port, _, _ = _parse_socks5("1.2.3.4:9999")
        self.assertEqual((host, port), ("1.2.3.4", 9999))

    def test_split_hostport(self):
        self.assertEqual(_split_hostport("1.2.3.4:8080"), ("1.2.3.4", 8080))
        self.assertEqual(_split_hostport("example.com"), ("example.com", 80))
        self.assertEqual(_split_hostport("[::1]:443"), ("::1", 443))


class SocksProxyRoutingTests(unittest.TestCase):
    def tearDown(self):
        browser_session._stop_socks_bridge()
        browser_session.configure(
            get_proxies=lambda: {},
            is_debug=lambda: False,
            is_headless=lambda: False,
        )

    def test_socks5_with_auth_routes_through_local_http_bridge(self):
        browser_session.configure(get_proxies=lambda: {"https": "socks5://u:p@1.2.3.4:7890"})
        opts = browser_session.create_browser_options(unique_profile=False)
        proxy = opts["proxy"]
        self.assertTrue(proxy["server"].startswith("http://127.0.0.1:"), proxy)
        self.assertNotIn("username", proxy)
        bridge = browser_session._tls.socks_bridge
        self.assertIsInstance(bridge, Socks5Bridge)

    def test_socks5_without_auth_routes_through_local_http_bridge(self):
        browser_session.configure(get_proxies=lambda: {"https": "socks5://9.9.9.9:1080"})
        opts = browser_session.create_browser_options(unique_profile=False)
        proxy = opts["proxy"]
        self.assertTrue(proxy["server"].startswith("http://127.0.0.1:"), proxy)

    def test_http_proxy_does_not_use_bridge(self):
        browser_session.configure(get_proxies=lambda: {"https": "http://1.2.3.4:8080"})
        opts = browser_session.create_browser_options(unique_profile=False)
        proxy = opts["proxy"]
        self.assertEqual(proxy["server"], "http://1.2.3.4:8080")
        self.assertNotIn("username", proxy)
        self.assertIsNone(getattr(browser_session._tls, "socks_bridge", None))

    def test_no_proxy_no_bridge(self):
        browser_session.configure(get_proxies=lambda: {})
        opts = browser_session.create_browser_options(unique_profile=False)
        self.assertNotIn("proxy", opts)
        self.assertIsNone(getattr(browser_session._tls, "socks_bridge", None))


if __name__ == "__main__":
    unittest.main()
