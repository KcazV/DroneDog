"""Network monitor: the single choke point for every outbound HTTP request DroneDog makes,
plus the operating system's view of the sockets held by this process and its WebView2
children (D11).

* All outbound requests MUST go through ``NetMonitor.request``. It refuses to run while
  online mode is off, records per host: purpose, request count, bytes out/in, last request
  time, errors, and the peer IPs it actually connected to.
* ``os_connections`` lists inet sockets owned by this process and every descendant
  (msedgewebview2.exe browser/renderer/utility processes) via psutil, classified as
  ``local`` (loopback, e.g. the 127.0.0.1 UI server) or ``remote``. A remote socket whose
  peer is not an IP we connected to through ``request`` is flagged ``unexpected`` (tamper
  evidence); while offline every remote socket is unexpected.

The webview itself cannot reach the network: index.html carries a CSP that only allows
'self', and app.py starts WebView2 with a dead proxy for non-loopback traffic.
"""

from __future__ import annotations

import http.client
import ipaddress
import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

USER_AGENT = "DroneDog/0.1 (forensic flight-log viewer; interactive use)"
DISCLOSURE = ("Only map tiles are requested. Tile requests reveal the map area being viewed to the "
              "provider. No evidence, hashes or case data are sent.")


class OfflineError(RuntimeError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip.split("%")[0]).is_loopback
    except ValueError:
        return ip in ("localhost",)


class NetMonitor:
    def __init__(self, user_agent: str = USER_AGENT):
        self.user_agent = user_agent
        self._lock = threading.RLock()
        self._online = False
        self._sources: list[str] = []
        self._hosts: dict[str, dict] = {}
        self._peer_ips: dict[str, str] = {}     # ip -> host
        self._changed_utc = _utc()

    # ---------------------------------------------------------------- state
    @property
    def online(self) -> bool:
        return self._online

    def set_online(self, on: bool, sources: list[str] | None = None) -> None:
        with self._lock:
            self._online = bool(on)
            self._sources = list(sources or []) if on else []
            self._changed_utc = _utc()

    # ---------------------------------------------------------------- choke point
    def request(self, url: str, purpose: str, headers: dict | None = None, timeout: float = 10.0,
                max_bytes: int = 8 * 1024 * 1024) -> tuple[int, dict, bytes]:
        """GET ``url``. Returns (status, lower-cased headers, body). Raises OfflineError when off."""
        if not self._online:
            raise OfflineError("online mode is off")
        parts = urlsplit(url)
        if parts.scheme not in ("https", "http"):
            raise ValueError(f"unsupported scheme {parts.scheme!r}")
        host = parts.hostname or ""
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        hdrs = {"User-Agent": self.user_agent, "Accept": "image/*,*/*;q=0.5", "Connection": "close"}
        hdrs.update(headers or {})
        out_bytes = len(f"GET {path} HTTP/1.1\r\nHost: {host}\r\n") + sum(len(k) + len(v) + 4 for k, v in hdrs.items()) + 2
        rec = self._host_rec(host, purpose)
        conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(host, parts.port, timeout=timeout)
        try:
            conn.request("GET", path, headers=hdrs)
            try:
                peer = conn.sock.getpeername()[0] if conn.sock else None
            except OSError:
                peer = None
            resp = conn.getresponse()
            body = resp.read(max_bytes + 1)
            status = resp.status
            rh = {k.lower(): v for k, v in resp.getheaders()}
            in_bytes = len(body) + sum(len(k) + len(v) + 4 for k, v in resp.getheaders()) + 17
            with self._lock:
                rec["requests"] += 1
                rec["bytes_out"] += out_bytes
                rec["bytes_in"] += in_bytes
                rec["last_utc"] = _utc()
                rec["last_status"] = status
                if status == 404:
                    rec["not_found"] += 1          # no tile at this place/zoom: normal, not an error
                elif status >= 400:
                    rec["errors"] += 1
                    rec["last_error"] = f"HTTP {status}"
                if peer:
                    rec["peers"] = sorted(set(rec["peers"]) | {peer})
                    self._peer_ips[peer] = host
            if len(body) > max_bytes:
                raise ValueError("response too large")
            return status, rh, body
        except Exception as e:  # noqa: BLE001
            with self._lock:
                rec["requests"] += 1
                rec["bytes_out"] += out_bytes
                rec["errors"] += 1
                rec["last_utc"] = _utc()
                rec["last_error"] = f"{type(e).__name__}: {e}"[:200]
            raise
        finally:
            conn.close()

    def _host_rec(self, host: str, purpose: str) -> dict:
        with self._lock:
            rec = self._hosts.get(host)
            if rec is None:
                rec = self._hosts[host] = {"host": host, "purpose": purpose, "requests": 0, "bytes_out": 0,
                                           "bytes_in": 0, "errors": 0, "not_found": 0, "last_utc": None, "last_status": None,
                                           "last_error": None, "peers": []}
            return rec

    # ---------------------------------------------------------------- OS view
    def os_connections(self) -> dict:
        try:
            import psutil
        except ImportError:
            return {"available": False, "error": "psutil not installed", "processes": [], "sockets": []}
        me = psutil.Process(os.getpid())
        procs = [me]
        try:
            procs += me.children(recursive=True)
        except psutil.Error:
            pass
        sockets, plist = [], []
        for p in procs:
            try:
                name = p.name()
                conns = p.net_connections(kind="inet")
            except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                plist.append({"pid": p.pid, "name": "?", "error": type(e).__name__})
                continue
            plist.append({"pid": p.pid, "name": name, "sockets": len(conns)})
            for c in conns:
                l = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else ""
                r = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else ""
                if not c.raddr:
                    scope = "local" if (not c.laddr or _is_loopback(c.laddr.ip)) else "listen"
                else:
                    scope = "local" if _is_loopback(c.raddr.ip) else "remote"
                host = self._peer_ips.get(c.raddr.ip) if c.raddr else None
                sockets.append({"pid": p.pid, "process": name, "laddr": l, "raddr": r, "status": c.status,
                                "scope": scope, "host": host,
                                "expected": scope != "remote" or (self._online and host is not None)})
        return {"available": True, "processes": plist, "sockets": sockets}

    # ---------------------------------------------------------------- summary
    def status(self, include_os: bool = True) -> dict:
        osv = self.os_connections()
        socks = osv.get("sockets", [])
        remote = [s for s in socks if s["scope"] in ("remote", "listen")]
        unexpected = [s for s in remote if not s["expected"]]
        with self._lock:
            hosts = [dict(h) for h in self._hosts.values()]
            online = self._online
            sources = list(self._sources)
        remote_hosts = sorted({s["host"] or s["raddr"].rsplit(":", 1)[0] for s in remote if s["raddr"]})
        state = "alert" if unexpected else ("online" if online or remote else "offline")
        out = {
            "state": state, "online": online, "sources": sources, "changed_utc": self._changed_utc,
            "remote_count": len(remote), "remote_hosts": remote_hosts,
            "hosts_contacted": len([h for h in hosts if h["requests"]]),
            "unexpected": unexpected, "hosts": hosts, "disclosure": DISCLOSURE,
            "os_available": osv.get("available", False), "os_error": osv.get("error"),
            "local_count": len([s for s in socks if s["scope"] == "local"]),
        }
        if include_os:
            out["os"] = osv
        return out
