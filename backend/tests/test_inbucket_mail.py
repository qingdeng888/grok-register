import unittest
from unittest import mock

from backend.mailbox import inbucket_mail as inbox
from backend.registration import engine as gr


class _FakeResp:
    def __init__(self, payload=None, status=200):
        self._payload = payload if payload is not None else []
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _no_cancel(cancel_callback=None):
    return


def _no_op_sleep(seconds, cancel_callback=None):
    return


class InbucketAdapterTests(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(gr.config)

    def tearDown(self):
        gr.config.clear()
        gr.config.update(self.original_config)

    def test_create_mailbox_requires_base_and_domain(self):
        with self.assertRaises(Exception):
            inbox.create_mailbox(domain="", base_url="")
        with self.assertRaises(Exception):
            inbox.create_mailbox(domain="example.com", base_url="")
        with self.assertRaises(Exception):
            inbox.create_mailbox(domain="", base_url="http://127.0.0.1:2500")

    def test_create_mailbox_builds_address(self):
        address, name = inbox.create_mailbox(
            domain="example.com", base_url="http://inbucket:2500", name="Abc_1"
        )
        self.assertEqual(address, "abc_1@example.com")
        self.assertEqual(name, "abc_1")

    def test_get_messages_parses_list_key(self):
        fake = _FakeResp({"messages": [{"id": "m1", "subject": "s"}]})
        msgs = inbox.get_messages(lambda *a, **k: fake, "http://inbucket:2500", "abc")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["id"], "m1")

    def test_get_message_detail_parses_body(self):
        detail = {
            "id": "m1",
            "subject": "Grok code",
            "header": {"To": "a@example.com"},
            "body": {"text": "Your code is A1B-C2D", "html": ""},
        }
        fake = _FakeResp(detail)
        got = inbox.get_message_detail(lambda *a, **k: fake, "http://inbucket:2500", "abc", "m1")
        self.assertEqual(got["id"], "m1")

    def test_wait_for_code_extracts_from_inbucket_detail(self):
        detail = {
            "id": "m1",
            "subject": "Verify",
            "header": {"To": "abc@example.com"},
            "body": {"text": "Your verification code is A1B-C2D", "html": ""},
        }
        calls = {"count": 0}

        def fake_get(url, headers=None):
            if url.endswith("/m1"):
                return _FakeResp(detail)
            calls["count"] += 1
            return _FakeResp({"messages": [{"id": "m1"}]})

        code = inbox.wait_for_code(
            fake_get,
            "http://inbucket:2500",
            "abc",
            "abc@example.com",
            timeout=10,
            poll_interval=1,
            raise_if_cancelled=_no_cancel,
            sleep_with_cancel=_no_op_sleep,
        )
        self.assertEqual(code, "A1B-C2D")

    def test_dispatch_in_get_email_and_token(self):
        gr.config["email_provider"] = "inbucket"
        gr.config["inbucket_base_url"] = "http://127.0.0.1:2500/"
        gr.config["inbucket_domain"] = "example.com"
        address, token = gr.get_email_and_token()
        self.assertTrue(address.endswith("@example.com"))
        self.assertTrue(token)

    def test_dispatch_in_get_oai_code(self):
        gr.config["email_provider"] = "inbucket"
        with mock.patch.object(inbox, "wait_for_code", return_value="A1B-C2D") as wf:
            code = gr.get_oai_code("abc", "abc@example.com", timeout=5, poll_interval=1)
        self.assertEqual(code, "A1B-C2D")
        wf.assert_called_once()
        _, kwargs = wf.call_args
        self.assertIn("raise_if_cancelled", kwargs)
        self.assertIn("sleep_with_cancel", kwargs)

    def test_default_config_has_inbucket_keys(self):
        self.assertIn("inbucket_base_url", gr.DEFAULT_CONFIG)
        self.assertIn("inbucket_api_key", gr.DEFAULT_CONFIG)
        self.assertIn("inbucket_domain", gr.DEFAULT_CONFIG)


if __name__ == "__main__":
    unittest.main()


class InbucketConfigSaveTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.original_config = dict(gr.config)
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmpfile.close()
        self.config_patcher = mock.patch.object(gr, "CONFIG_FILE", self.tmpfile.name)
        self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()
        import os
        try:
            os.unlink(self.tmpfile.name)
        except OSError:
            pass
        gr.config.clear()
        gr.config.update(self.original_config)

    def test_apply_config_updates_keeps_inbucket_provider(self):
        from backend.web.application import _apply_config_updates

        res = _apply_config_updates(
            {
                "email_provider": "inbucket",
                "inbucket_base_url": "http://inbucket:2500",
                "inbucket_domain": "mail.example.com",
            }
        )
        self.assertIn("email_provider", res["changed"])
        self.assertEqual(gr.config.get("email_provider"), "inbucket")
        self.assertEqual(gr.config.get("inbucket_base_url"), "http://inbucket:2500")
        self.assertEqual(gr.config.get("inbucket_domain"), "mail.example.com")

    def test_apply_config_updates_rejects_unknown_provider(self):
        from backend.web.application import _apply_config_updates

        _apply_config_updates({"email_provider": "not-a-provider"})
        self.assertEqual(gr.config.get("email_provider"), "cloudflare")


class InbucketWildcardTests(unittest.TestCase):
    def test_expand_wildcard_domain_leaves_plain_domain(self):
        self.assertEqual(inbox.expand_wildcard_domain("mail.example.com"), "mail.example.com")
        self.assertEqual(inbox.expand_wildcard_domain(""), "")

    def test_expand_wildcard_domain_replaces_star(self):
        d = inbox.expand_wildcard_domain("*.mail.xiaoy.de")
        first = d.split(".")[0]
        self.assertTrue(d.endswith(".mail.xiaoy.de"))
        self.assertEqual(len(first), 8)
        self.assertNotEqual(first, "*")

    def test_expand_wildcard_domain_nested(self):
        d = inbox.expand_wildcard_domain("*.sub.*.x")
        labels = d.split(".")
        self.assertEqual(labels[1], "sub")
        self.assertEqual(labels[-1], "x")
        self.assertNotEqual(labels[0], "*")
        self.assertNotEqual(labels[2], "*")

    def test_create_mailbox_uses_expanded_domain(self):
        address, _name = inbox.create_mailbox(
            domain="*.mail.xiaoy.de", base_url="http://inbucket:2500", name="Abc_1"
        )
        self.assertTrue(address.endswith(".mail.xiaoy.de"))
        self.assertNotIn("*", address)
