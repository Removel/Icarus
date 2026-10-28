"""Local Fetch checks; remote DNS/redirect enforcement belongs to the fetcher."""

import ipaddress
import re
from urllib.parse import urlsplit


def validate_fetch_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 8192:
        raise ValueError("url must be a nonblank HTTP(S) URL within 8192 characters")
    value = value.strip()
    if any(ord(c) <= 32 or ord(c) == 127 or c == "\\" for c in value):
        raise ValueError("url contains unsafe characters")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
        if parsed.scheme not in {"http", "https"} or not host or parsed.username is not None or parsed.password is not None:
            raise ValueError
        if port is not None and port != (443 if parsed.scheme == "https" else 80):
            raise ValueError
        if "%" in host:
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", host):
                raise ValueError
            host = host.encode("idna").decode("ascii")
            if "." not in host:
                raise ValueError
            if host.endswith((".localhost", ".local", ".internal", ".lan", ".home", ".arpa")):
                raise ValueError
            if len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
                raise ValueError
        else:
            if not address.is_global or address.is_multicast or address.is_reserved:
                raise ValueError
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
                raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError("Fetch requires a public HTTP(S) URL on its standard port, without credentials") from None
    return value
