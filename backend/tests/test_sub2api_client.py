# -*- coding: utf-8 -*-
"""Sub2API 客户端单元测试：请求头、body 裁剪与错误截断。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.integrations import sub2api_client
from backend.integrations.sub2api_client import Sub2APIClient, Sub2APIError


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text


    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, dict(kwargs)))
        return self.responses.pop(0)

    def close(self):
        self.closed = True


class Sub2APIClientTests(unittest.TestCase):
    def test_owned_session_does_not_inherit_environment_proxy(self):
        session = mock.Mock()
        with mock.patch.object(
            sub2api_client.requests,
            "Session",
            return_value=session,
        ) as factory:
            client = Sub2APIClient("https://example.test", "secret-key")
        factory.assert_called_once_with(trust_env=False)
        self.assertIs(client.session, session)

    def test_is_configured_requires_url_and_key(self):
        self.assertFalse(Sub2APIClient.is_configured({}))
        self.assertFalse(
            Sub2APIClient.is_configured(
                {"sub2api_remote_url": "https://example.test", "sub2api_api_key": ""}
            )
        )
        self.assertTrue(
            Sub2APIClient.is_configured(
                {
                    "sub2api_remote_url": "https://example.test/",
                    "sub2api_api_key": "k",
                }
            )
        )

    def test_from_config_normalizes_base_url(self):
        client = Sub2APIClient.from_config(
            {
                "sub2api_remote_url": "https://example.test/",
                "sub2api_api_key": "secret",
            },
            session=FakeSession([]),
        )
        self.assertEqual(client.base_url, "https://example.test")

    def test_parse_group_ids_drops_non_digits(self):
        self.assertEqual(Sub2APIClient.parse_group_ids("1,abc,2,,3"), [1, 2, 3])
        self.assertEqual(Sub2APIClient.parse_group_ids(""), [])
        self.assertEqual(Sub2APIClient.parse_group_ids(None), [])

    def test_sso_to_oauth_sends_x_api_key_and_trims_optional_fields(self):
        session = FakeSession(
            [FakeResponse(payload={"created": [{"id": 1}], "failed": []})]
        )
        client = Sub2APIClient(
            "https://example.test/", "admin-key", session=session
        )
        result = client.sso_to_oauth(
            ["sso-token"],
            name="",
            proxy_id=0,
            group_ids=[],
            concurrency=1,
            priority=0,
        )
        self.assertEqual(result["created"], [{"id": 1}])
        url, kwargs = session.calls[0]
        self.assertEqual(
            url, "https://example.test/api/v1/admin/grok/sso-to-oauth"
        )
        self.assertEqual(kwargs["headers"]["x-api-key"], "admin-key")
        body = kwargs["json"]
        self.assertEqual(body["sso_tokens"], ["sso-token"])
        self.assertNotIn("proxy_id", body)
        self.assertNotIn("group_ids", body)
        self.assertNotIn("name", body)
        self.assertEqual(body["concurrency"], 1)
        self.assertEqual(body["priority"], 0)

    def test_sso_to_oauth_includes_valid_optional_fields(self):
        session = FakeSession(
            [FakeResponse(payload={"data": {"created": ["a"], "failed": []}})]
        )
        client = Sub2APIClient(
            "https://example.test", "admin-key", session=session
        )
        client.sso_to_oauth(
            ["sso-1"],
            name="prefix",
            proxy_id=7,
            group_ids=[1, 2],
            concurrency=4,
            priority=-3,
        )
        body = session.calls[0][1]["json"]
        self.assertEqual(body["name"], "prefix")
        self.assertEqual(body["proxy_id"], 7)
        self.assertEqual(body["group_ids"], [1, 2])
        self.assertEqual(body["concurrency"], 4)
        self.assertEqual(body["priority"], -3)

    def test_sso_to_oauth_http_error_truncates_body(self):
        long_body = "x" * 500
        session = FakeSession([FakeResponse(status=500, text=long_body)])
        client = Sub2APIClient(
            "https://example.test", "admin-key", session=session
        )
        with self.assertRaises(Sub2APIError) as ctx:
            client.sso_to_oauth(["sso"])
        message = str(ctx.exception)
        self.assertIn("HTTP 500", message)
        self.assertTrue(message.endswith("..."))
        self.assertLess(len(message), 400)

    def test_sso_to_oauth_rejects_empty_tokens(self):
        client = Sub2APIClient(
            "https://example.test", "admin-key", session=FakeSession([])
        )
        with self.assertRaisesRegex(Sub2APIError, "sso_tokens 为空"):
            client.sso_to_oauth(["", "  "])

    def test_context_manager_closes_owned_session_only(self):
        external = FakeSession([])
        client = Sub2APIClient(
            "https://example.test", "admin-key", session=external
        )
        client.close()
        self.assertFalse(external.closed)


class Sub2APIEngineIntegrationTests(unittest.TestCase):
    """add_sso_to_cpa 内 Sub2API 分支的轻量集成测试。"""

    def setUp(self):
        from backend.registration import engine

        self.engine = engine
        self.original = dict(engine.config)

    def tearDown(self):
        self.engine.config.clear()
        self.engine.config.update(self.original)

    def _base_config(self, **overrides):
        cfg = {
            "cpa_auto_add": True,
            "cpa_auth_dir": "",
            "cpa_remote_url": "",
            "cpa_management_key": "",
            "cpa_upload_enabled": True,
            "grok2api_auth_dir": "",
            "grok2api_auto_import": False,
            "cpa_token_mode": "device_protocol",
            "proxy": "",
            "sub2api_enabled": False,
            "sub2api_remote_url": "",
            "sub2api_api_key": "",
            "sub2api_group_ids": "",
            "sub2api_proxy_id": 0,
            "sub2api_concurrency": 1,
            "sub2api_priority": 0,
            "sub2api_name_prefix": "",
        }
        cfg.update(overrides)
        return cfg

    def test_sub2api_disabled_marks_status_and_skips_request(self):
        g2a_dir = tempfile.mkdtemp()
        self.engine.config.update(
            self._base_config(grok2api_auth_dir=g2a_dir, sub2api_enabled=False)
        )
        token = {
            "access_token": "eyJhbGciOiJub25lIn0.eyJzdWIiOiJhIn0.",
            "refresh_token": "r",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        result = {}
        with (
            mock.patch.object(self.engine._s2cpa, "sso_to_token", return_value=token),
            mock.patch.object(
                self.engine._s2cpa,
                "token_to_cpa_record",
                return_value={"access_token": token["access_token"], "email": "a@b.c"},
            ),
            mock.patch.object(
                self.engine._s2cpa,
                "write_grok2api_auth_bundle",
                return_value={"grok_build": Path(g2a_dir) / "g.json"},
            ),
            mock.patch.object(
                self.engine._grok2api.Grok2APIClient,
                "is_configured",
                return_value=False,
            ),
            mock.patch.object(
                self.engine._sub2api.Sub2APIClient,
                "from_config",
            ) as from_config,
        ):
            ok = self.engine.add_sso_to_cpa(
                "sso-token", email="a@b.c", result_out=result
            )
        self.assertTrue(ok)
        self.assertEqual(result.get("sub2api_remote_status"), "disabled")
        from_config.assert_not_called()

    def test_sub2api_success_records_status(self):
        g2a_dir = tempfile.mkdtemp()
        self.engine.config.update(
            self._base_config(
                grok2api_auth_dir=g2a_dir,
                sub2api_enabled=True,
                sub2api_remote_url="https://sub2.example",
                sub2api_api_key="k",
                sub2api_name_prefix="pref",
            )
        )
        token = {
            "access_token": "eyJhbGciOiJub25lIn0.eyJzdWIiOiJhIn0.",
            "refresh_token": "r",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        fake_client = mock.MagicMock()
        fake_client.__enter__.return_value = fake_client
        fake_client.__exit__.return_value = False
        fake_client.sso_to_oauth.return_value = {
            "created": [{"id": 9}],
            "failed": [],
        }
        result = {}
        with (
            mock.patch.object(self.engine._s2cpa, "sso_to_token", return_value=token),
            mock.patch.object(
                self.engine._s2cpa,
                "token_to_cpa_record",
                return_value={"access_token": token["access_token"], "email": "a@b.c"},
            ),
            mock.patch.object(
                self.engine._s2cpa,
                "write_grok2api_auth_bundle",
                return_value={"grok_build": Path(g2a_dir) / "g.json"},
            ),
            mock.patch.object(
                self.engine._grok2api.Grok2APIClient,
                "is_configured",
                return_value=False,
            ),
            mock.patch.object(
                self.engine._sub2api.Sub2APIClient,
                "from_config",
                return_value=fake_client,
            ),
        ):
            ok = self.engine.add_sso_to_cpa(
                "sso-token", email="a@b.c", result_out=result
            )
        self.assertTrue(ok)
        self.assertEqual(result.get("sub2api_remote_status"), "success")
        self.assertTrue(result.get("sub2api_remote_imported_at"))
        fake_client.sso_to_oauth.assert_called_once()
        call_kwargs = fake_client.sso_to_oauth.call_args
        self.assertEqual(call_kwargs.args[0], ["sso-token"])
        self.assertEqual(call_kwargs.kwargs.get("name"), "pref")

    def test_token_exchange_failure_skips_sub2api(self):
        self.engine.config.update(
            self._base_config(
                cpa_auth_dir="/tmp/cpa-not-used",
                sub2api_enabled=True,
                sub2api_remote_url="https://sub2.example",
                sub2api_api_key="k",
            )
        )
        result = {}
        with (
            mock.patch.object(self.engine._s2cpa, "sso_to_token", return_value=None),
            mock.patch.object(
                self.engine, "_append_sso_pending", return_value=None
            ),
            mock.patch.object(
                self.engine._sub2api.Sub2APIClient,
                "from_config",
            ) as from_config,
        ):
            ok = self.engine.add_sso_to_cpa(
                "sso-token", email="a@b.c", result_out=result
            )
        self.assertFalse(ok)
        self.assertEqual(result.get("status"), "failed")
        from_config.assert_not_called()

    def test_cpa_upload_disabled_skips_remote_upload(self):
        auth_dir = tempfile.mkdtemp()
        self.engine.config.update(
            self._base_config(
                cpa_auth_dir=auth_dir,
                cpa_remote_url="http://cpa.internal:8317",
                cpa_management_key="mk",
                cpa_upload_enabled=False,
            )
        )
        token = {
            "access_token": "eyJhbGciOiJub25lIn0.eyJzdWIiOiJhIn0.",
            "refresh_token": "r",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        result = {}
        logs = []
        with (
            mock.patch.object(self.engine._s2cpa, "sso_to_token", return_value=token),
            mock.patch.object(
                self.engine._s2cpa,
                "token_to_cpa_record",
                return_value={"access_token": token["access_token"], "email": "a@b.c"},
            ),
            mock.patch.object(
                self.engine._s2cpa,
                "write_cpa_auth",
                return_value=Path(auth_dir) / "cpa.json",
            ),
            mock.patch.object(
                self.engine._s2cpa,
                "upload_cpa_auth_remote",
            ) as upload,
            mock.patch.object(
                self.engine._grok2api.Grok2APIClient,
                "is_configured",
                return_value=False,
            ),
        ):
            ok = self.engine.add_sso_to_cpa(
                "sso-token",
                email="a@b.c",
                log_callback=logs.append,
                result_out=result,
            )
        self.assertTrue(ok)
        upload.assert_not_called()
        self.assertEqual(result.get("cpa_remote_status"), "disabled")
        self.assertTrue(any("上传开关关闭" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
