"""本地 HTTP 代理桥：把带认证的 SOCKS5 上游转成无认证 HTTP 代理。

Firefox/Camoufox 的 Playwright 代理参数不支持 SOCKS5 认证（会报
``Browser does not support socks5 proxy authentication``），但支持普通
HTTP 代理。该桥在 127.0.0.1 上起一个无认证 HTTP 代理，内部把请求转发到
SOCKS5 上游（含账号密码），从而让 Camoufox 能走带认证的 SOCKS5。

只应监听回环地址，仅用于当前进程内 Camoufox 浏览器。
"""

from __future__ import annotations

import socket
import struct
import threading
from typing import Optional, Tuple
from urllib.parse import urlsplit


class Socks5Bridge:
    """在 ``127.0.0.1:<port>`` 提供 HTTP CONNECT/绝对URI 代理，转发到 SOCKS5 上游。"""

    def __init__(self, socks5_url: str, listen_host: str = "127.0.0.1", listen_port: int = 0):
        self.socks_host, self.socks_port, self.username, self.password = _parse_socks5(socks5_url)
        self.listen_host = listen_host
        self.listen_port = listen_port
        self._server: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> Tuple[str, int]:
        with self._lock:
            if self._server is not None:
                return self.listen_host, self.listen_port
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.listen_host, self.listen_port))
            server.listen(128)
            server.settimeout(0.5)
            self._server = server
            self.listen_port = server.getsockname()[1]
            self._stop.clear()
            self._thread = threading.Thread(target=self._serve, daemon=True)
            self._thread.start()
            return self.listen_host, self.listen_port

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            server, self._server = self._server, None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(30)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                data += chunk
                if len(data) > 64 * 1024:
                    return
            head, _, rest = data.partition(b"\r\n\r\n")
            request_line = head.split(b"\r\n", 1)[0].decode("latin1", "replace")
            parts = request_line.split(" ")
            if len(parts) < 2:
                return
            method, target = parts[0], parts[1]

            if method.upper() == "CONNECT":
                host, port = _split_hostport(target)
                up = _socks5_connect(
                    self.socks_host, self.socks_port, host, port,
                    self.username, self.password,
                )
                conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                _relay(conn, up)
                return

            # 普通 HTTP：绝对 URI 或相对路径
            if target.startswith("http://") or target.startswith("https://"):
                u = urlsplit(target)
                host, port = u.hostname, (u.port or (443 if u.scheme == "https" else 80))
                # 绝对 URI 的请求行需改写为路径形式，否则源站可能拒绝
                path = u.path or "/"
                if u.query:
                    path += "?" + u.query
                data = head.replace(
                    ("%s %s" % (method, target)).encode("latin1"),
                    ("%s %s" % (method, path)).encode("latin1"),
                    1,
                ) + b"\r\n\r\n" + rest
            else:
                host = _header_value(head, b"Host")
                if not host:
                    return
                host, port = _split_hostport(host)
            up = _socks5_connect(
                self.socks_host, self.socks_port, host, port,
                self.username, self.password,
            )
            up.sendall(data)  # 含请求头与已读到的 body 片段
            _relay(up, conn)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


def _header_value(head: bytes, name: bytes) -> str:
    for line in head.split(b"\r\n")[1:]:
        k, _, v = line.partition(b":")
        if k.strip().lower() == name.lower():
            return v.strip().decode("latin1", "replace")
    return ""


def _split_hostport(hostport: str) -> Tuple[str, int]:
    hostport = hostport.strip()
    if hostport.startswith("["):  # IPv6
        host, _, port = hostport[1:].partition("]")
        port = port.lstrip(":").strip()
        return host, int(port) if port else 443
    if ":" in hostport:
        host, _, port = hostport.rpartition(":")
        return host, int(port)
    return hostport, 80


def _parse_socks5(url: str) -> Tuple[str, int, Optional[str], Optional[str]]:
    u = urlsplit(url if "://" in url else f"socks5://{url}")
    host = u.hostname or ""
    port = u.port or 1080
    user = u.username
    password = u.password
    if not host:
        raise ValueError("SOCKS5 代理缺少主机地址")
    return host, port, user, password


def _socks5_connect(
    socks_host: str,
    socks_port: int,
    target_host: str,
    target_port: int,
    username: Optional[str],
    password: Optional[str],
) -> socket.socket:
    s = socket.create_connection((socks_host, socks_port), timeout=15)
    s.settimeout(30)
    if username or password:
        s.sendall(b"\x05\x01\x02")  # 仅支持账号密码认证
        resp = s.recv(2)
        if resp != b"\x05\x02":
            raise RuntimeError("SOCKS5 服务器不要求账号密码认证")
        ub = username.encode() if username else b""
        pb = password.encode() if password else b""
        s.sendall(b"\x01" + bytes([len(ub)]) + ub + bytes([len(pb)]) + pb)
        # RFC1929 用户名/密码认证成功响应为 0x01 0x00（版本号=1，状态=0）
        if s.recv(2) != b"\x01\x00":
            raise RuntimeError("SOCKS5 认证失败")
    else:
        s.sendall(b"\x05\x01\x00")
        if s.recv(2) != b"\x05\x00":
            raise RuntimeError("SOCKS5 无认证协商失败")
    try:
        target_host.encode("ascii").decode("idna")
        use_domain = False
        ip = socket.inet_aton(target_host)
    except Exception:
        ip = None
        use_domain = True
    if use_domain:
        hb = target_host.encode("idna")
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + struct.pack(">H", target_port))
    else:
        s.sendall(b"\x05\x01\x00\x01" + ip + struct.pack(">H", target_port))
    resp = s.recv(4)
    if len(resp) != 4 or resp[0] != 0x05 or resp[1] != 0x00:
        raise RuntimeError(f"SOCKS5 连接目标失败: {resp!r}")
    atyp = resp[3]
    if atyp == 1:
        s.recv(4 + 2)
    elif atyp == 3:
        ln = s.recv(1)[0]
        s.recv(ln + 2)
    elif atyp == 4:
        s.recv(16 + 2)
    return s


def _relay(a: socket.socket, b: socket.socket) -> None:
    def pump(src, dst):
        try:
            while True:
                d = src.recv(65536)
                if not d:
                    break
                dst.sendall(d)
        except Exception:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    t1 = threading.Thread(target=pump, args=(a, b), daemon=True)
    t2 = threading.Thread(target=pump, args=(b, a), daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=120)
    t2.join(timeout=120)
