"""Provider subscriptions: fetch privately, detect dummy/expired payloads, and merge node state."""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import socket
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

SUPPORTED_SCHEMES = ("vless://", "trojan://", "hysteria2://", "hy2://")
DUMMY_HOSTS = frozenset({None, "", "0.0.0.0", "127.0.0.1", "localhost", "::", "::1"})
DUMMY_KEYWORDS = (
    "не обновлено",
    "не поддержив",
    "истекла",
    "истёк",
    "оплатите",
    "заблокирован",
    "осталось дней",
    "expired",
    "update app",
    "not supported",
    "subscription expired",
)
SNIPPET_LIMIT = 28
DEFAULT_POOLS = {"general": False, "gemini": False, "ru": False, "lte": False}
DEFAULT_PROBES = {"general": None, "gemini": None, "ru": None}


def _read_hwid(base: Path | None = None) -> str:
    candidates = [Path("/root/.secrets/vpn-provider-hwid")]
    if base is not None:
        candidates.insert(0, base / ".vpn-provider-hwid")
    for path in candidates:
        try:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""


def validate_subscription_url(url: str, resolve_dns: bool = False) -> str:
    """Validate scheme (`http`/`https`) and block loopback/private/link-local targets (SSRF guard)."""
    clean = str(url or "").strip()
    parsed = urllib.parse.urlsplit(clean)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL подписки должен начинаться с http:// или https://")
    host = parsed.hostname.strip().lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("Локальные адреса запрещены в URL подписки")

    def _check_ip(ip_str: str) -> None:
        addr = ipaddress.ip_address(ip_str)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved or addr.is_unspecified:
            raise ValueError("Приватные и локальные IP-адреса запрещены в URL подписки")

    try:
        _check_ip(host)
    except ValueError as exc:
        if "запрещены" in str(exc):
            raise
        if resolve_dns:
            try:
                infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
            except OSError:
                return clean
            for info in infos:
                sockaddr = info[4]
                if sockaddr:
                    _check_ip(str(sockaddr[0]))
    return clean


def normalize_tg_url(tg_url: str) -> str:
    """Normalize Telegram link strictly to `https://t.me/<path>` or empty string (XSS guard)."""
    raw = str(tg_url or "").strip()
    if not raw:
        return ""
    if raw.startswith("@"):
        raw = "https://t.me/" + raw[1:]
    elif raw.lower().startswith("t.me/"):
        raw = "https://" + raw
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() not in {"t.me", "telegram.me"}:
        raise ValueError("Ссылка на ТГ должна быть в формате t.me/... или https://t.me/...")
    path = parsed.path.lstrip("/")
    if not path or any(ch in path for ch in ("<", ">", '"', "'", " ")):
        raise ValueError("Некорректная ссылка на ТГ")
    return f"https://t.me/{path}"


def build_headers(user_agent: str = "Happ/2.11.0", hwid: str = "") -> dict[str, str]:
    headers = {
        "User-Agent": user_agent,
        "x-device-os": "iOS",
        "x-ver-os": "18.1",
    }
    if hwid:
        headers["x-hwid"] = hwid
    return headers


def parse_userinfo(header_value: str | None) -> dict[str, int]:
    """Parse `subscription-userinfo: upload=...; download=...; total=...; expire=...`."""
    result = {"upload": 0, "download": 0, "total": 0, "expire": 0}
    if not header_value:
        return result
    for part in str(header_value).split(";"):
        key, _, val = part.strip().partition("=")
        key, val = key.strip().lower(), val.strip()
        if key in result and val.isdigit():
            result[key] = int(val)
    return result


def make_snippet(text: str, limit: int = SNIPPET_LIMIT) -> str:
    clean = " ".join(str(text).split())
    if not clean:
        return ""
    if len(clean) <= limit:
        return clean
    return clean[:limit].rstrip() + "…"


def decode_subscription_links(raw: bytes) -> list[str]:
    """Decode either raw or base64 list of proxy links (`vless://`, `trojan://`, `hysteria2://`, `hy2://`)."""
    payload = raw.strip()
    if not payload or payload.startswith((b"{", b"[", b"<")):
        return []
    if not any(scheme.encode("ascii") in payload for scheme in SUPPORTED_SCHEMES):
        try:
            padded = payload + b"=" * (-len(payload) % 4)
            payload = base64.b64decode(padded, validate=False)
        except Exception:
            return []
    try:
        text = payload.decode("utf-8", "strict")
    except UnicodeDecodeError:
        text = payload.decode("utf-8", "replace")
    links = []
    for line in text.splitlines():
        item = line.strip()
        if item.startswith(SUPPORTED_SCHEMES):
            links.append(item)
    return links


def is_dummy_link(link: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(link)
    except ValueError:
        return True
    host = (parsed.hostname or "").strip().lower()
    if host in DUMMY_HOSTS:
        return True
    if parsed.port is not None and parsed.port <= 1:
        return True
    name = urllib.parse.unquote(parsed.fragment or "").strip().casefold()
    return any(keyword in name for keyword in DUMMY_KEYWORDS)


def transport_fingerprint(link: str) -> tuple[str, str, int | None, str, str, str, str]:
    """Extract full transport identity `(scheme, host, port, type, security, sni_or_host, path_or_pbk_or_service)`."""
    u = urllib.parse.urlsplit(link)
    scheme = (u.scheme or "").lower()
    if scheme == "hy2":
        scheme = "hysteria2"
    host = (u.hostname or "").lower()
    port = u.port
    q = dict(urllib.parse.parse_qsl(u.query))
    net = q.get("type", "tcp").lower()
    sec = q.get("security", "").lower()
    sni = (q.get("sni") or q.get("host") or host).lower()
    extra = f"{q.get('path', '')}|{q.get('serviceName', '')}|{q.get('pbk', '')}|{q.get('sid', '')}|{q.get('obfs', '')}"
    return (scheme, host, port, net, sec, sni, extra)


def make_node_id(link: str, original_name: str | None = None, sub_id: str = "") -> str:
    """Deterministic node ID scoped to `sub_id` and full transport parameters (prevents cross-sub collisions)."""
    fp = transport_fingerprint(link)
    parsed = urllib.parse.urlsplit(link)
    name = (original_name if original_name is not None else urllib.parse.unquote(parsed.fragment or "")).strip()
    ident = f"{sub_id}|{fp[0]}://{fp[1]}:{fp[2]}|{fp[3]}|{fp[4]}|{fp[5]}|{fp[6]}|{name}"
    return "n_" + hashlib.sha256(ident.encode("utf-8")).hexdigest()[:16]


def inspect_payload(
    raw: bytes,
    userinfo_header: str | None = None,
    http_status: int = 200,
    now: int | None = None,
) -> dict[str, Any]:
    """Classify a provider response into active nodes (with dummy info-nodes filtered out) or an error."""
    now_ts = int(time.time() if now is None else now)
    info = parse_userinfo(userinfo_header)
    expire = info["expire"]

    if http_status != 200:
        msg = f"HTTP {http_status}"
        return {
            "status": "error",
            "error_snippet": make_snippet(msg),
            "error_full": msg,
            "expire": expire,
            "userinfo": info,
            "links": [],
        }

    links = decode_subscription_links(raw)
    if not links:
        msg = "Пустой ответ или неподдерживаемый формат подписки"
        return {
            "status": "error",
            "error_snippet": make_snippet(msg),
            "error_full": msg,
            "expire": expire,
            "userinfo": info,
            "links": [],
        }

    dummy_flags = [is_dummy_link(link) for link in links]
    if all(dummy_flags):
        messages = []
        for link in links:
            frag = urllib.parse.unquote(urllib.parse.urlsplit(link).fragment or "").strip()
            if frag and frag not in messages:
                messages.append(frag)
        full_text = "\n".join(messages) if messages else "Провайдер вернул заглушку вместо списка серверов"
        return {
            "status": "error",
            "error_snippet": make_snippet(full_text),
            "error_full": full_text,
            "expire": expire,
            "userinfo": info,
            "links": [],
        }

    valid_links = [link for link, is_dummy in zip(links, dummy_flags) if not is_dummy]
    if expire > 0 and expire < now_ts:
        msg = "Срок действия подписки истёк"
        return {
            "status": "error",
            "error_snippet": make_snippet(msg),
            "error_full": msg,
            "expire": expire,
            "userinfo": info,
            "links": [],
        }

    return {
        "status": "active",
        "error_snippet": "",
        "error_full": "",
        "expire": expire,
        "userinfo": info,
        "links": valid_links,
    }


def merge_nodes(
    existing_nodes: list[dict[str, Any]] | None,
    fresh_links: list[str],
    sub_id: str = "",
) -> list[dict[str, Any]]:
    """Merge fresh links with previous node records.

    Crucial invariant: when a node matches an existing record (by exact ID, by original_name,
    or by full transport fingerprint), it MUST keep its previous `node_id` so references in
    `policy.json` (`primary` / `fallbacks`) never become dangling after a provider rename or port change.
    """
    existing = existing_nodes or []
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    by_fp: dict[tuple[str, str, int | None, str, str, str, str], list[dict[str, Any]]] = {}

    for node in existing:
        if not isinstance(node, dict):
            continue
        nid = str(node.get("node_id") or "").strip()
        if nid:
            by_id[nid] = node
        orig = str(node.get("original_name") or "").strip()
        if orig:
            by_name.setdefault(orig, []).append(node)
        old_link = str(node.get("link") or "")
        if old_link:
            by_fp.setdefault(transport_fingerprint(old_link), []).append(node)

    merged: list[dict[str, Any]] = []
    used_prev_ids: set[str] = set()
    seen_new_ids: set[str] = set()

    def _take_unused(candidates: list[dict[str, Any]] | None) -> dict[str, Any] | None:
        for cand in candidates or []:
            cid = str(cand.get("node_id") or "")
            if cid not in used_prev_ids:
                return cand
        return None

    for link in fresh_links:
        if is_dummy_link(link):
            continue
        u = urllib.parse.urlsplit(link)
        orig_name = urllib.parse.unquote(u.fragment or "").strip() or (u.hostname or "Сервер")
        candidate_id = make_node_id(link, orig_name, sub_id=sub_id)
        fp = transport_fingerprint(link)

        prev = None
        if candidate_id in by_id and candidate_id not in used_prev_ids:
            prev = by_id[candidate_id]
        if prev is None:
            prev = _take_unused(by_name.get(orig_name))
        if prev is None:
            prev = _take_unused(by_fp.get(fp))

        if prev is not None and prev.get("node_id"):
            nid = str(prev["node_id"])
            used_prev_ids.add(nid)
        else:
            nid = candidate_id

        if nid in seen_new_ids:
            continue
        seen_new_ids.add(nid)

        old_orig = str(prev.get("original_name") or "").strip() if prev else ""
        old_custom = str(prev.get("custom_name") or "").strip() if prev else ""
        if prev and old_custom and old_custom != old_orig:
            custom_name = old_custom
        else:
            custom_name = orig_name

        prev_pools = (prev.get("pools") if prev and isinstance(prev.get("pools"), dict) else {}) or {}
        pools = {k: bool(prev_pools.get(k, False)) for k in DEFAULT_POOLS}

        prev_probe = (prev.get("last_probe") if prev and isinstance(prev.get("last_probe"), dict) else {}) or {}
        last_probe = {k: prev_probe.get(k) for k in DEFAULT_PROBES}

        merged.append(
            {
                "node_id": nid,
                "link": link,
                "original_name": orig_name,
                "custom_name": custom_name,
                "pools": pools,
                "last_probe": last_probe,
            }
        )
    return merged


def _default_http_get(url: str, headers: dict[str, str], timeout: int = 12) -> tuple[int, str, bytes]:
    validate_subscription_url(url, resolve_dns=True)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Ответ провайдера превышает 2 МБ")
        userinfo = resp.headers.get("subscription-userinfo", "")
        return int(getattr(resp, "status", 200)), userinfo, raw


def fetch_subscription(
    url: str,
    hwid: str = "",
    timeout: int = 12,
    http_get: Callable[[str, dict[str, str], int], tuple[int, str, bytes]] | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Fetch a subscription URL using Happ / v2rayTun headers and return inspected payload."""
    try:
        validate_subscription_url(url, resolve_dns=(http_get is None))
    except ValueError as exc:
        msg = str(exc)
        return {
            "status": "error",
            "error_snippet": make_snippet(msg),
            "error_full": msg,
            "expire": 0,
            "userinfo": parse_userinfo(""),
            "links": [],
        }

    getter = http_get or _default_http_get
    last_result: dict[str, Any] | None = None
    for ua in ("Happ/2.11.0", "v2rayTun/4.7.0"):
        headers = build_headers(user_agent=ua, hwid=hwid)
        try:
            status, userinfo, raw = getter(url, headers, timeout)
        except Exception as exc:
            msg = f"Ошибка подключения: {type(exc).__name__}"
            last_result = {
                "status": "error",
                "error_snippet": make_snippet(msg),
                "error_full": msg,
                "expire": 0,
                "userinfo": parse_userinfo(""),
                "links": [],
            }
            continue
        inspected = inspect_payload(raw, userinfo_header=userinfo, http_status=status, now=now)
        if inspected["status"] == "active":
            return inspected
        if last_result is None or inspected.get("error_full") != "Пустой ответ или неподдерживаемый формат подписки":
            last_result = inspected
    return last_result or {
        "status": "error",
        "error_snippet": make_snippet("Не удалось получить подписку"),
        "error_full": "Не удалось получить подписку",
        "expire": 0,
        "userinfo": parse_userinfo(""),
        "links": [],
    }


def _atomic_write_json(path: Path, data: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def ensure_sub_schema(item: dict[str, Any], index: int = 0) -> dict[str, Any]:
    """Upgrade a legacy `{name, url}` entry in-memory to the full subscription schema."""
    sub_id = str(item.get("id") or "").strip()
    if not sub_id:
        seed = f"{item.get('name', '')}:{item.get('url', '')}:{index}"
        sub_id = "sub_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
    return {
        "id": sub_id,
        "name": str(item.get("name") or f"Подписка {index + 1}").strip(),
        "url": str(item.get("url") or "").strip(),
        "tg_url": str(item.get("tg_url") or "").strip(),
        "status": str(item.get("status") or "active"),
        "error_snippet": str(item.get("error_snippet") or ""),
        "error_full": str(item.get("error_full") or ""),
        "expire": int(item.get("expire") or 0),
        "updated_at": int(item.get("updated_at") or 0),
        "nodes": list(item.get("nodes") or []),
    }


def update_subscription_entry(
    entry: dict[str, Any],
    hwid: str = "",
    timeout: int = 12,
    http_get: Callable[[str, dict[str, str], int], tuple[int, str, bytes]] | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Fetch fresh data for a single subscription dict and update its fields without wiping nodes on error."""
    now_ts = int(time.time() if now is None else now)
    sub = ensure_sub_schema(entry)
    fetched = fetch_subscription(sub["url"], hwid=hwid, timeout=timeout, http_get=http_get, now=now_ts)

    sub["status"] = fetched["status"]
    sub["error_snippet"] = fetched["error_snippet"]
    sub["error_full"] = fetched["error_full"]
    if fetched["expire"] > 0 or sub["expire"] == 0:
        sub["expire"] = fetched["expire"]
    sub["updated_at"] = now_ts

    if fetched["status"] == "active":
        sub["nodes"] = merge_nodes(sub.get("nodes"), fetched["links"], sub_id=sub["id"])
    return sub


def entries(base: Path) -> list[dict[str, str]]:
    """Backward-compatible helper for route_control.py: enumerate non-LTE links from subs.json or cache."""
    result: list[dict[str, str]] = []
    subs_path = base / "subs.json"
    if subs_path.exists():
        try:
            subs = json.loads(subs_path.read_text(encoding="utf-8"))
            for sub in subs:
                for node in sub.get("nodes") or []:
                    link = str(node.get("link") or "").strip()
                    if not link:
                        continue
                    u = urllib.parse.urlsplit(link)
                    name = str(node.get("custom_name") or node.get("original_name") or urllib.parse.unquote(u.fragment)).strip()
                    if "lte" not in name.casefold() and u.scheme in {"vless", "trojan", "hysteria2", "hy2"}:
                        result.append({"link": link, "name": name, "node_id": node.get("node_id", "")})
        except (OSError, ValueError, TypeError):
            pass
    if result:
        return result

    root = base / "provider-subscriptions"
    if root.exists():
        for path in sorted(root.glob("subscription-*.json")):
            try:
                links = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for link in links:
                u = urllib.parse.urlsplit(link)
                name = urllib.parse.unquote(u.fragment).strip()
                if "lte" not in name.casefold() and u.scheme in {"vless", "trojan", "hysteria2", "hy2"}:
                    result.append({"link": link, "name": name})
    return result


def refresh(
    base: Path,
    cancel_event=None,
    http_get: Callable[[str, dict[str, str], int], tuple[int, str, bytes]] | None = None,
    now: int | None = None,
) -> list[int]:
    """Refresh all subscriptions in `base / subs.json` and update `provider-subscriptions/subscription-<i>.json`."""
    subs_path = base / "subs.json"
    raw_sources = json.loads(subs_path.read_text(encoding="utf-8"))
    hwid = _read_hwid(base)
    directory = base / "provider-subscriptions"
    directory.mkdir(mode=0o700, exist_ok=True)

    failures: list[int] = []
    updated_subs: list[dict[str, Any]] = []

    for index, source in enumerate(raw_sources):
        sub = ensure_sub_schema(source, index=index)
        if cancel_event is not None and cancel_event.is_set():
            updated_subs.append(sub)
            continue

        if not sub["nodes"]:
            cached_file = directory / f"subscription-{index}.json"
            if cached_file.exists():
                try:
                    cached_links = json.loads(cached_file.read_text(encoding="utf-8"))
                    if isinstance(cached_links, list):
                        sub["nodes"] = merge_nodes([], [str(x) for x in cached_links if isinstance(x, str)], sub_id=sub["id"])
                except (OSError, ValueError):
                    pass

        sub = update_subscription_entry(sub, hwid=hwid, timeout=8, http_get=http_get, now=now)
        updated_subs.append(sub)

        if sub["status"] != "active":
            failures.append(index)
            continue

        links = [n["link"] for n in sub["nodes"] if n.get("link")]
        _atomic_write_json(directory / f"subscription-{index}.json", links, mode=0o600)

    _atomic_write_json(subs_path, updated_subs, mode=0o600)
    return failures
