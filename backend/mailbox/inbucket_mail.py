"""Inbucket 自建邮箱渠道适配器。

Inbucket 是自托管 SMTP/POP3 测试邮箱服务，提供 REST API：
  - GET    /api/v1/mailbox/{name}            列出某邮箱的邮件
  - GET    /api/v1/mailbox/{name}/{id}       读取单封邮件详情
Inbucket 不主动创建邮箱，邮件投递到收信域名时才自动生成，因此本模块
只负责生成随机地址，并轮询验证码邮件。
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

from backend.mailbox.utilities import extract_verification_code, generate_username, strip_html

HttpGet = Callable[..., Any]

API_BASE_DEFAULT = "http://127.0.0.1:2500"


def normalize_base(base_url: str = "") -> str:
    base = str(base_url or "").strip().rstrip("/")
    return base or ""


def normalize_domain(domain: str = "") -> str:
    return str(domain or "").strip().lstrip("@").strip(".")


def mailbox_name_from_email(email: str) -> str:
    return str(email or "").split("@", 1)[0].strip()


def expand_wildcard_domain(domain: str = "") -> str:
    """把域名里的 ``*`` 替换成随机子域标签，实现泛域名。

    例如 ``*.mail.example.com`` -> ``a1b2c3d4.mail.example.com``；
    每个 ``*`` 独立展开为不同随机标签，嵌套子域也能用。
    """
    dom = normalize_domain(domain)
    if "*" not in dom:
        return dom
    return re.sub(r"\*", lambda _m: generate_username(8), dom)


def build_headers(api_key: str = "") -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    if str(api_key or "").strip():
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def create_mailbox(
    domain: str = "",
    name: str = "",
    base_url: str = "",
) -> tuple[str, str]:
    """生成一个 Inbucket 地址，返回 (address, mailbox_name)。

    Inbucket 在邮件送达时才创建邮箱，因此这里只拼地址；mailbox_name
    作为收信标识，兼作注册流程所需的一个非空 token 占位。
    """
    base = normalize_base(base_url)
    if not base:
        raise Exception("Inbucket Base URL 未配置")
    dom = expand_wildcard_domain(domain)
    if not dom:
        raise Exception("Inbucket 收信域名未配置")
    local = (name or generate_username(10)).strip().lower()
    local = re.sub(r"[^a-z0-9._-]+", "", local)
    if not local:
        raise Exception("Inbucket 邮箱名无效")
    address = f"{local}@{dom}"
    return address, local


def get_messages(
    http_get: HttpGet,
    base_url: str,
    mailbox_name: str,
    api_key: str = "",
) -> List[dict]:
    base = normalize_base(base_url)
    if not base:
        raise Exception("Inbucket Base URL 未配置")
    name = mailbox_name_from_email(mailbox_name)
    if not name:
        raise Exception("Inbucket 邮箱名无效")
    resp = http_get(
        f"{base}/api/v1/mailbox/{quote(name, safe='')}",
        headers=build_headers(api_key),
    )
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, dict):
        messages = payload.get("messages") or payload.get("items") or []
    else:
        messages = payload or []
    return [m for m in messages if isinstance(m, dict)]


def get_message_detail(
    http_get: HttpGet,
    base_url: str,
    mailbox_name: str,
    message_id: str,
    api_key: str = "",
) -> dict:
    base = normalize_base(base_url)
    name = mailbox_name_from_email(mailbox_name)
    if not name:
        raise Exception("Inbucket 邮箱名无效")
    resp = http_get(
        f"{base}/api/v1/mailbox/{quote(name, safe='')}/{quote(str(message_id), safe='')}",
        headers=build_headers(api_key),
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload if isinstance(payload, dict) else {}


def _message_text(detail: dict) -> tuple[str, str]:
    """从 Inbucket 详情里提取 (subject, 纯文本正文)。"""
    detail = detail or {}
    header = detail.get("header") if isinstance(detail.get("header"), dict) else {}
    subject = str(detail.get("subject") or header.get("Subject") or "")
    body = detail.get("body") if isinstance(detail.get("body"), dict) else {}
    parts = []
    text = body.get("text") or detail.get("text") or ""
    if isinstance(text, str) and text.strip():
        parts.append(text)
    html = body.get("html") or detail.get("html") or ""
    if isinstance(html, str) and html.strip():
        parts.append(strip_html(html))
    return subject, "\n".join(parts)


def wait_for_code(
    http_get: HttpGet,
    base_url: str,
    mailbox_name: str,
    email: str,
    *,
    timeout: int = 180,
    poll_interval: int = 3,
    extract_code: Callable[[str, str], Optional[str]] = extract_verification_code,
    raise_if_cancelled: Callable[[Optional[Callable[[], bool]]], None],
    sleep_with_cancel: Callable[[float, Optional[Callable[[], bool]]], None],
    log_callback: Optional[Callable[[str], None]] = None,
    cancel_callback: Optional[Callable[[], bool]] = None,
    api_key: str = "",
) -> str:
    name = mailbox_name_from_email(mailbox_name or email)
    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            messages = get_messages(http_get, base_url, name, api_key=api_key)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] Inbucket 拉取邮件列表失败: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        for msg in messages:
            msg_id = msg.get("id") or msg.get("message_id") or msg.get("msgid")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            try:
                detail = get_message_detail(
                    http_get, base_url, name, msg_id, api_key=api_key
                )
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] Inbucket 获取邮件详情失败: {exc}")
                continue
            subject, combined = _message_text(detail)
            if log_callback:
                log_callback(f"[Debug] Inbucket 收到邮件: {subject}")
            code = extract_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] Inbucket 从邮件中提取到验证码: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"Inbucket 在 {timeout}s 内未收到验证码邮件")
