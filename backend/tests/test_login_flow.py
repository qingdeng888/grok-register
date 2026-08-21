import unittest
from unittest import mock

from backend.registration import login_flow


class _Page:
    def __init__(self, error=None):
        self.error = error
        self.url = "https://accounts.x.ai/sign-in"
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error


class LoginNavigationTests(unittest.TestCase):
    def test_navigation_waits_only_for_dom_content(self):
        page = _Page()
        with (
            mock.patch.object(login_flow, "_active_or_new_page", return_value=page),
            mock.patch.object(
                login_flow,
                "_wait_for_signin_page",
                return_value={
                    "url": page.url,
                    "ready": True,
                    "region_blocked": False,
                    "text": "Log into your account",
                },
            ),
        ):
            login_flow._navigate_signin()

        self.assertEqual(
            page.calls,
            [
                (
                    login_flow.SIGNIN_URL,
                    {
                        "wait_until": "domcontentloaded",
                        "timeout": login_flow.SIGNIN_NAVIGATION_TIMEOUT_MS,
                    },
                )
            ],
        )

    def test_navigation_timeout_is_soft_when_login_ui_is_ready(self):
        page = _Page(TimeoutError("fixture load timeout"))
        logs = []
        with (
            mock.patch.object(login_flow, "_active_or_new_page", return_value=page),
            mock.patch.object(
                login_flow,
                "_wait_for_signin_page",
                return_value={
                    "url": page.url,
                    "ready": True,
                    "region_blocked": False,
                    "text": "Log into your account",
                },
            ),
        ):
            login_flow._navigate_signin(log_callback=logs.append)

        self.assertTrue(any("登录控件已经可用" in message for message in logs))

    def test_region_block_restarts_browser_and_recovers(self):
        first = _Page()
        second = _Page()
        logs = []
        states = [
            {
                "url": first.url,
                "ready": False,
                "region_blocked": True,
                "text": "This service is not available in your region.",
            },
            {
                "url": second.url,
                "ready": True,
                "region_blocked": False,
                "text": "Log into your account",
            },
        ]
        with (
            mock.patch.object(
                login_flow,
                "_active_or_new_page",
                side_effect=[first, second],
            ) as acquire,
            mock.patch.object(
                login_flow,
                "_wait_for_signin_page",
                side_effect=states,
            ),
        ):
            login_flow._navigate_signin(log_callback=logs.append)

        self.assertFalse(acquire.call_args_list[0].kwargs["restart"])
        self.assertTrue(acquire.call_args_list[1].kwargs["restart"])
        self.assertTrue(any("代理出口地区不可用" in message for message in logs))

    def test_repeated_region_block_reports_specific_reason(self):
        page = _Page()
        state = {
            "url": page.url,
            "ready": False,
            "region_blocked": True,
            "text": "This service is not available in your region.",
        }
        with (
            mock.patch.object(login_flow, "SIGNIN_NAVIGATION_ATTEMPTS", 2),
            mock.patch.object(login_flow, "_active_or_new_page", return_value=page),
            mock.patch.object(login_flow, "_wait_for_signin_page", return_value=state),
        ):
            with self.assertRaisesRegex(RuntimeError, "代理出口地区不可用"):
                login_flow._navigate_signin()


class LoginFormTests(unittest.TestCase):
    def test_password_field_retry_uses_explicit_kind(self):
        locator = mock.Mock()
        locator.input_value.return_value = ""
        element = mock.Mock(_raw=locator)
        fresh_locator = mock.Mock()
        fresh_locator.input_value.return_value = "fixture@password"
        fresh = mock.Mock(_raw=fresh_locator)

        with mock.patch.object(
            login_flow,
            "_native_input_candidates",
            return_value=[fresh],
        ) as candidates:
            self.assertTrue(
                login_flow._type_login_value(
                    element,
                    "fixture@password",
                    kind="password",
                )
            )

        candidates.assert_called_once_with("password")

    def test_type_value_retries_when_first_input_is_dropped(self):
        # 复刻真实故障：邮箱框刚渲染，首轮输入被受控组件冲掉（读回为空），
        # 重抓句柄再输入才稳住。验证会重试而非一次失败。
        stale = mock.Mock()
        stale._raw.input_value.return_value = ""  # 首轮写入被冲掉

        settled = mock.Mock()
        # 第二轮：click/fill/press 后读回正确值。
        settled._raw.input_value.return_value = "fixture@example.com"

        # 每次 _native_input_candidates 调用返回的候选：先给 stale（读回仍空），再给 settled。
        candidate_batches = iter([[stale], [settled], [settled]])

        with (
            mock.patch.object(
                login_flow, "_native_input_candidates",
                side_effect=lambda kind: next(candidate_batches),
            ),
            mock.patch.object(login_flow.time, "sleep"),
        ):
            ok = login_flow._type_login_value(
                stale, "fixture@example.com", kind="email", attempts=4
            )

        self.assertTrue(ok)

    def test_type_value_fails_after_exhausting_attempts(self):
        dead = mock.Mock()
        dead._raw.input_value.return_value = ""

        with (
            mock.patch.object(login_flow, "_native_input_candidates", return_value=[dead]),
            mock.patch.object(login_flow.time, "sleep"),
        ):
            ok = login_flow._type_login_value(dead, "x@y.com", kind="email", attempts=3)

        self.assertFalse(ok)

    def test_existing_email_form_is_resumed_without_clicking_entry_button(self):
        email_input = mock.Mock()
        password_input = mock.Mock()
        active = mock.Mock(url="https://grok.com/")

        def inputs(kind):
            return [email_input] if kind == "email" else [password_input]

        with (
            mock.patch.object(login_flow, "_navigate_signin"),
            mock.patch.object(login_flow, "_dismiss_cookie_consent"),
            mock.patch.object(login_flow, "_native_input_candidates", side_effect=inputs),
            mock.patch.object(login_flow, "_native_click_action") as entry_click,
            mock.patch.object(login_flow, "_type_login_value", return_value=True),
            mock.patch.object(login_flow, "_click_submit", return_value=True),
            mock.patch.object(login_flow, "_try_sync_turnstile", return_value=True),
            mock.patch.object(login_flow, "_visible_login_error", return_value=""),
            mock.patch.object(login_flow, "active_page", return_value=active),
            mock.patch.object(login_flow, "_wait_for_login_sso", return_value="sso-value"),
            mock.patch.object(login_flow.time, "sleep"),
        ):
            token = login_flow.login_with_password(
                "fixture@example.com",
                "fixture-password",
            )

        self.assertEqual(token, "sso-value")
        entry_click.assert_not_called()

    def test_single_page_form_skips_next_and_submits_login(self):
        # 单页表单：填完邮箱后密码框已在场，不应点“下一步”，直接填密码并提交登录。
        email_input = mock.Mock()
        password_input = mock.Mock()
        active = mock.Mock(url="https://grok.com/")

        def inputs(kind):
            return [email_input] if kind == "email" else [password_input]

        with (
            mock.patch.object(login_flow, "_navigate_signin"),
            mock.patch.object(login_flow, "_dismiss_cookie_consent"),
            mock.patch.object(login_flow, "_reveal_email_input", return_value=[email_input]),
            mock.patch.object(login_flow, "_native_input_candidates", side_effect=inputs),
            mock.patch.object(login_flow, "_type_login_value", return_value=True),
            mock.patch.object(login_flow, "_click_submit", return_value=True) as click_submit,
            mock.patch.object(login_flow, "_try_sync_turnstile", return_value=True),
            mock.patch.object(login_flow, "_visible_login_error", return_value=""),
            mock.patch.object(login_flow, "active_page", return_value=active),
            mock.patch.object(login_flow, "_wait_for_login_sso", return_value="sso-value"),
            mock.patch.object(login_flow.time, "sleep"),
        ):
            token = login_flow.login_with_password("fixture@example.com", "pw")

        self.assertEqual(token, "sso-value")
        # 只应有一次提交（Login），不应先点“下一步”。
        click_submit.assert_called_once()
        self.assertNotIn(("下一步", "next", "continue"), [c.args[0] for c in click_submit.call_args_list])

    def test_stepwise_form_clicks_next_before_password(self):
        # 分步表单：填完邮箱后密码框尚未出现，先点“下一步”，密码框才出现。
        email_input = mock.Mock()
        password_input = mock.Mock()
        active = mock.Mock(url="https://grok.com/")
        # password 首查为空（触发点“下一步”），点击后再查已出现。
        password_states = iter([[], [password_input], [password_input]])

        def inputs(kind):
            return [email_input] if kind == "email" else next(password_states)

        with (
            mock.patch.object(login_flow, "_navigate_signin"),
            mock.patch.object(login_flow, "_dismiss_cookie_consent"),
            mock.patch.object(login_flow, "_reveal_email_input", return_value=[email_input]),
            mock.patch.object(login_flow, "_native_input_candidates", side_effect=inputs),
            mock.patch.object(login_flow, "_type_login_value", return_value=True),
            mock.patch.object(login_flow, "_click_submit", return_value=True) as click_submit,
            mock.patch.object(login_flow, "_wait_until", return_value=True),
            mock.patch.object(login_flow, "_try_sync_turnstile", return_value=True),
            mock.patch.object(login_flow, "_visible_login_error", return_value=""),
            mock.patch.object(login_flow, "active_page", return_value=active),
            mock.patch.object(login_flow, "_wait_for_login_sso", return_value="sso-value"),
            mock.patch.object(login_flow.time, "sleep"),
        ):
            token = login_flow.login_with_password("fixture@example.com", "pw")

        self.assertEqual(token, "sso-value")
        submitted_keywords = [c.args[0] for c in click_submit.call_args_list]
        # 先点“下一步”推进，再点“登录”提交。
        self.assertIn(("下一步", "next", "continue"), submitted_keywords)
        self.assertEqual(click_submit.call_count, 2)


class RevealEmailInputTests(unittest.TestCase):
    def test_click_is_retried_until_email_input_appears(self):
        # 首次点击“假成功”（框未出现），第二次点击后邮箱框才出现。
        inputs_seq = iter([[], ["email-input"]])

        with (
            mock.patch.object(
                login_flow, "_native_input_candidates", side_effect=lambda kind: next(inputs_seq)
            ),
            mock.patch.object(login_flow, "_dismiss_cookie_consent") as dismiss,
            mock.patch.object(login_flow, "_native_click_action", return_value="Login with email") as click,
            mock.patch.object(login_flow, "_wait_until", side_effect=[False, True]),
            mock.patch.object(login_flow.time, "sleep"),
        ):
            result = login_flow._reveal_email_input()

        self.assertEqual(result, ["email-input"])
        self.assertEqual(click.call_count, 2)
        # 每轮点击前都重新关闭迟到的 Cookie 横幅。
        self.assertEqual(dismiss.call_count, 2)

    def test_existing_email_input_skips_click_entirely(self):
        with (
            mock.patch.object(login_flow, "_native_input_candidates", return_value=["email-input"]),
            mock.patch.object(login_flow, "_native_click_action") as click,
        ):
            result = login_flow._reveal_email_input()

        self.assertEqual(result, ["email-input"])
        click.assert_not_called()

    def test_persistent_failure_raises_after_all_attempts(self):
        with (
            mock.patch.object(login_flow, "_native_input_candidates", return_value=[]),
            mock.patch.object(login_flow, "_dismiss_cookie_consent"),
            mock.patch.object(login_flow, "_native_click_action", return_value="Login with email") as click,
            mock.patch.object(login_flow, "_wait_until", return_value=False),
            mock.patch.object(login_flow.time, "sleep"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                login_flow._reveal_email_input()

        self.assertIn("邮箱输入框", str(ctx.exception))
        self.assertEqual(click.call_count, login_flow.EMAIL_STEP_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
