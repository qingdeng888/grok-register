# -*- coding: utf-8 -*-
"""已注册账号的重新登录流程。

仅使用现有邮箱和密码打开登录页并刷新 SSO，不进入注册页，也不访问邮箱服务。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from backend.automation.session import (
    active_browser,
    active_page,
    page,
    restart_browser,
    set_browser_session,
    start_browser,
)
from backend.registration.signup_flow import (
    _dismiss_cookie_consent,
    _native_click_action,
    _native_input_candidates,
    _try_sync_turnstile,
)


SIGNIN_URL = "https://accounts.x.ai/sign-in"
SIGNIN_NAVIGATION_ATTEMPTS = 3
SIGNIN_NAVIGATION_TIMEOUT_MS = 45_000


def _wait_until(predicate: Callable[[], bool], timeout: float, interval: float = 0.25) -> bool:
    deadline = time.time() + max(float(timeout or 0), 0)
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def _visible_login_error() -> str:
    """提取登录表单中的短错误提示，避免把整页条款当成错误。"""
    try:
        value = page.run_js(
            r"""
const selectors = [
  '[role="alert"]', '[aria-live="assertive"]', '[aria-live="polite"]',
  '[data-testid*="error" i]', '[class*="error" i]'
];
for (const selector of selectors) {
  for (const node of document.querySelectorAll(selector)) {
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    if (style.display === 'none' || style.visibility === 'hidden' || !rect.width || !rect.height) continue;
    const text = (node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim();
    if (text && text.length <= 300 && !['邮箱','密码','登录','Email','Password','Sign in','Log in'].includes(text)) return text;
  }
}
return '';
            """
        )
    except Exception:
        return ""
    text = str(value or "").strip()
    # 页面会把字段标题（例如“邮箱”）挂在 error class 上；它不是登录失败原因。
    if text in {"邮箱", "密码", "登录", "Email", "Password", "Sign in", "Log in"}:
        return ""
    return text


def capture_login_diagnostics() -> dict:
    """读取登录失败现场的低敏诊断信息，不包含输入框值和密码。"""
    diagnostics = {"url": "", "title": "", "visible_error": "", "page_text": "", "controls": ""}
    try:
        value = page.run_js(
            r"""
const visible = (node) => {
  const style = getComputedStyle(node);
  const rect = node.getBoundingClientRect();
  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
};
const clean = (value, max = 1200) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, max);
const errors = [];
for (const selector of ['[role="alert"]','[aria-live="assertive"]','[aria-live="polite"]','[data-testid*="error" i]','[class*="error" i]']) {
  for (const node of document.querySelectorAll(selector)) {
    if (!visible(node)) continue;
    const text = clean(node.innerText || node.textContent);
    if (text && !['邮箱','密码','登录','Email','Password','Sign in','Log in'].includes(text) && !errors.includes(text)) errors.push(text);
  }
}
const controls = [...document.querySelectorAll('input,button,[role="button"]')]
  .filter(visible)
  .map(node => {
    const tag = node.tagName.toLowerCase();
    const type = node.getAttribute('type') || '';
    const label = node.getAttribute('aria-label') || node.getAttribute('placeholder') || node.innerText || node.textContent || '';
    const invalid = node.getAttribute('aria-invalid') || '';
    return `${tag}${type ? `[${type}]` : ''}: ${clean(label, 180)}${invalid ? ` (invalid=${invalid})` : ''}`;
  }).filter(Boolean).slice(0, 20).join(' | ');
return {
  url: String(location.href || ''),
  title: clean(document.title, 200),
  visible_error: errors.join(' | ').slice(0, 600),
  page_text: clean(document.body && document.body.innerText, 1800),
  controls,
};
"""
        )
        if isinstance(value, dict):
            diagnostics.update({key: str(value.get(key) or "") for key in diagnostics})
    except Exception:
        pass
    return diagnostics


EMAIL_STEP_ATTEMPTS = 4
EMAIL_STEP_CLICK_WAIT = 6.0


def _reveal_email_input(log_callback=None):
    """点击“使用邮箱登录”并等待邮箱框出现，失败则重试。

    单次点击常因 React 事件尚未挂载、或迟到的 Cookie 横幅拦截而“假成功”，
    与注册流程 click_email_signup_button 保持同等健壮性：每轮重新关闭横幅并重点。
    """
    email_inputs = _native_input_candidates("email")
    if email_inputs:
        return email_inputs

    last_error = "登录页未出现邮箱输入框"
    for attempt in range(1, EMAIL_STEP_ATTEMPTS + 1):
        # Cookie SDK 可能在登录页加载后才挂载并遮挡按钮，点击前必须再次关闭。
        try:
            _dismiss_cookie_consent(log_callback)
        except Exception:
            pass
        clicked = _native_click_action(
            ("login with email", "使用邮箱登录", "邮箱登录", "continue with email"),
            deny_keywords=("sign up", "注册"),
        )
        if not clicked:
            last_error = "登录页未找到“使用邮箱登录”按钮"
            if _native_input_candidates("email"):
                return _native_input_candidates("email")
        elif _wait_until(lambda: bool(_native_input_candidates("email")), EMAIL_STEP_CLICK_WAIT):
            return _native_input_candidates("email")
        else:
            last_error = "登录页未出现邮箱输入框"

        if attempt < EMAIL_STEP_ATTEMPTS and log_callback:
            log_callback(
                f"[!] {last_error}，重试点击“使用邮箱登录” ({attempt + 1}/{EMAIL_STEP_ATTEMPTS})"
            )
    raise RuntimeError(last_error)


def _click_submit(keywords) -> bool:
    """优先点击稳定的 sign-in-submit，再回退到可见文案。"""
    # Cookie SDK 可能在表单出现后才挂载，提交前必须再次关闭。
    try:
        _dismiss_cookie_consent()
    except Exception:
        pass
    try:
        buttons = page.eles('[data-testid="sign-in-submit"]') or []
    except Exception:
        buttons = []
    for button in buttons:
        try:
            if button.states.is_displayed and button.states.is_enabled:
                # force 仍走 Playwright 鼠标事件，但不被延迟出现的 Cookie 横幅拦截。
                button._raw.click(force=True, timeout=5000)
                return True
        except Exception:
            continue
    return bool(
        _native_click_action(
            keywords,
            deny_keywords=("注册", "sign up", "google", "apple", "login with x"),
        )
    )


def _type_login_value(element, value: str, *, kind: str, log_callback=None, attempts: int = 4) -> bool:
    """使用 Playwright 键盘输入并校验；失败则重抓句柄重试。

    邮箱框刚由 SPA 渲染出来时仍在挂载/重渲染，首次输入常被受控组件冲掉或句柄失效。
    每轮重新获取当前稳定元素再输入，短暂间隔让页面 settle，直到读回值与目标一致。
    """
    text = str(value or "")
    target = text.strip()
    current_element = element
    for attempt in range(1, max(int(attempts or 1), 1) + 1):
        if current_element is None:
            candidates = _native_input_candidates(kind)
            current_element = candidates[0] if candidates else None
        if current_element is not None:
            try:
                locator = current_element._raw
                locator.click(force=True, timeout=5000)
                locator.fill("", force=True)
                locator.press_sequentially(text, delay=45)
                if str(locator.input_value() or "").strip() == target:
                    return True
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 登录框真实输入异常: {type(exc).__name__}: {exc}")
        # 页面重渲染后旧句柄可能失效，下一轮重新抓取当前稳定元素。
        current_element = None
        fresh = _native_input_candidates(kind)
        if fresh:
            try:
                if str(fresh[0]._raw.input_value() or "").strip() == target:
                    return True
            except Exception:
                pass
            current_element = fresh[0]
        if attempt < attempts:
            time.sleep(0.6)
    return False


def _signin_page_state(page_obj) -> dict:
    """Return whether the sign-in UI is usable or blocked by the current route."""
    state = {"url": "", "ready": False, "region_blocked": False, "text": ""}
    try:
        value = page_obj.run_js(
            r"""
const visible = (node) => {
  if (!node) return false;
  const style = getComputedStyle(node);
  const rect = node.getBoundingClientRect();
  return style.display !== 'none' && style.visibility !== 'hidden'
    && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
};
const text = String(document.body && document.body.innerText || '')
  .replace(/\s+/g, ' ').trim().slice(0, 1200);
const controls = [...document.querySelectorAll('button,a,[role="button"]')]
  .filter(visible)
  .map((node) => String(node.innerText || node.textContent || node.getAttribute('aria-label') || ''))
  .join(' ').toLowerCase();
const hasEmailInput = [...document.querySelectorAll('input')].some((node) => {
  if (!visible(node)) return false;
  const meta = [node.type, node.name, node.autocomplete, node.placeholder, node.getAttribute('data-testid')]
    .filter(Boolean).join(' ').toLowerCase();
  return meta.includes('email');
});
return {
  url: String(location.href || ''),
  text,
  ready: hasEmailInput || controls.includes('login with email')
    || controls.includes('continue with email') || controls.includes('使用邮箱登录'),
};
"""
        )
        if isinstance(value, dict):
            state["url"] = str(value.get("url") or "")
            state["text"] = str(value.get("text") or "")
            state["ready"] = bool(value.get("ready"))
    except Exception:
        state["url"] = str(getattr(page_obj, "url", "") or "")
    lower_text = state["text"].lower()
    state["region_blocked"] = "service is not available in your region" in lower_text
    return state


def _wait_for_signin_page(page_obj, timeout: float = 12) -> dict:
    deadline = time.time() + max(float(timeout or 0), 0)
    state = _signin_page_state(page_obj)
    while not state["ready"] and not state["region_blocked"] and time.time() < deadline:
        time.sleep(0.25)
        state = _signin_page_state(page_obj)
    return state


def _active_or_new_page(log_callback=None, *, restart: bool = False):
    if restart:
        restart_browser(log_callback=log_callback)
    elif active_browser() is None:
        start_browser(log_callback=log_callback)
    browser_obj = active_browser()
    if browser_obj is None:
        raise RuntimeError("浏览器启动后未返回活动实例")
    tabs = browser_obj.get_tabs()
    page_obj = tabs[-1] if tabs else browser_obj.new_tab()
    set_browser_session(browser_obj, page_obj)
    return page_obj


def _navigate_signin(log_callback=None) -> None:
    last_error = ""
    for attempt in range(1, SIGNIN_NAVIGATION_ATTEMPTS + 1):
        page_obj = _active_or_new_page(
            log_callback=log_callback,
            restart=attempt > 1,
        )
        navigation_error = ""
        try:
            # 完整 load 会被慢代理或第三方资源长期拖住；登录控件只依赖 DOM 就绪。
            page_obj.get(
                SIGNIN_URL,
                wait_until="domcontentloaded",
                timeout=SIGNIN_NAVIGATION_TIMEOUT_MS,
            )
        except Exception as exc:
            navigation_error = f"{type(exc).__name__}: {exc}"

        state = _wait_for_signin_page(page_obj)
        current = state["url"] or str(getattr(page_obj, "url", "") or "")
        if state["ready"] and "accounts.x.ai" in current:
            if navigation_error and log_callback:
                log_callback(
                    "[Debug] 登录页导航等待异常，但登录控件已经可用，继续执行: "
                    f"{navigation_error[:240]}"
                )
            return

        if state["region_blocked"]:
            last_error = "当前代理出口地区不可用"
        elif "accounts.x.ai" not in current:
            last_error = f"打开登录页后进入了异常地址: {current or 'empty'}"
        elif navigation_error:
            last_error = f"登录页导航失败: {navigation_error}"
        else:
            preview = state["text"].replace("\n", " ").strip()[:180]
            last_error = f"登录页已打开但未出现可用控件: {preview or 'empty page'}"

        if attempt < SIGNIN_NAVIGATION_ATTEMPTS and log_callback:
            log_callback(
                f"[!] {last_error}，重启浏览器切换代理连接后重试 "
                f"({attempt + 1}/{SIGNIN_NAVIGATION_ATTEMPTS})"
            )

    raise RuntimeError(last_error or "打开登录页失败")


def _read_sso_cookie() -> str:
    try:
        cookies = page.cookies(all_domains=True, all_info=True) or []
    except Exception:
        return ""
    fallback = ""
    for item in cookies:
        if isinstance(item, dict):
            name = str(item.get("name") or "")
            value = str(item.get("value") or "")
        else:
            name = str(getattr(item, "name", "") or "")
            value = str(getattr(item, "value", "") or "")
        if name == "sso" and value:
            return value
        if name == "sso-rw" and value:
            fallback = value
    return fallback


def _wait_for_login_sso(timeout: int, log_callback=None) -> str:
    """自然等待登录跳转与 SSO，期间不主动导航到 grok.com。"""
    deadline = time.time() + max(int(timeout or 90), 30)
    last_log = 0.0
    while time.time() < deadline:
        token = _read_sso_cookie()
        if token:
            if log_callback:
                log_callback("[*] 重新登录成功，已获取新的 sso cookie")
            return token
        now = time.time()
        if log_callback and now - last_log >= 5:
            last_log = now
            current = str(getattr(active_page(), "url", "") or "")
            log_callback(f"[*] 等待重新登录 SSO... 剩余 {max(int(deadline - now), 0)}s | url={current[:90]}")
        error = _visible_login_error()
        if error:
            raise RuntimeError(f"登录失败: {error}")
        time.sleep(0.4)
    current = str(getattr(active_page(), "url", "") or "")
    raise RuntimeError(f"重新登录超时，未获取到 SSO；当前 URL: {current[:120]}")


def login_with_password(
    email: str,
    password: str,
    *,
    timeout: int = 90,
    log_callback=None,
) -> str:
    """使用邮箱密码登录并返回新的 SSO cookie。"""
    normalized_email = str(email or "").strip()
    secret = str(password or "")
    if not normalized_email or "@" not in normalized_email:
        raise ValueError("账号记录缺少有效邮箱")
    if not secret:
        raise ValueError("账号记录缺少密码")

    _navigate_signin(log_callback=log_callback)
    _dismiss_cookie_consent(log_callback)
    if log_callback:
        log_callback(f"[*] 打开重新登录页: {normalized_email}")

    email_inputs = _reveal_email_input(log_callback=log_callback)
    if not email_inputs or not _type_login_value(
        email_inputs[0],
        normalized_email,
        kind="email",
        log_callback=log_callback,
    ):
        raise RuntimeError("邮箱输入失败")
    # 站点存在两种登录表单:
    #   单页表单——邮箱与密码同页,填完邮箱后密码框已可见,且提交按钮在密码为空时禁用;
    #   分步表单——需先点「下一步」推进,密码框才会出现。
    # 填完邮箱后先探测密码框:已在则直接填密码,缺失才走「下一步」推进,兼容两种表单。
    password_inputs = _native_input_candidates("password")
    if not password_inputs:
        if not _click_submit(("下一步", "next", "continue")):
            raise RuntimeError("邮箱页未找到下一步按钮")
        if not _wait_until(lambda: bool(_native_input_candidates("password")), 15):
            detail = _visible_login_error()
            raise RuntimeError(detail or "登录页未出现密码输入框")
        password_inputs = _native_input_candidates("password")
    if not password_inputs or not _type_login_value(
        password_inputs[0],
        secret,
        kind="password",
        log_callback=log_callback,
    ):
        raise RuntimeError("密码输入失败")
    if not _try_sync_turnstile(
        log_callback=log_callback,
        cancel_callback=None,
        reason="等待登录安全验证",
    ):
        raise RuntimeError("登录安全验证未通过")
    if not _click_submit(("登录", "sign in", "log in", "continue")):
        raise RuntimeError("密码页未找到登录按钮")

    # 先给表单错误一个快速反馈窗口；正常成功会立即进入 redirect。
    for _ in range(12):
        time.sleep(0.25)
        error = _visible_login_error()
        if error:
            raise RuntimeError(f"登录失败: {error}")
        try:
            if "sign-in" not in str(active_page().url or ""):
                break
        except Exception:
            break

    return _wait_for_login_sso(timeout=max(int(timeout or 90), 30), log_callback=log_callback)


def capture_login_failure(path: Path) -> str:
    """保存重新登录失败现场；返回已写入路径。"""
    page_obj = active_page()
    if page_obj is None:
        return ""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        page_obj.screenshot(path=str(path), full_page=True)
    except Exception:
        return ""
    return str(path) if path.is_file() else ""
