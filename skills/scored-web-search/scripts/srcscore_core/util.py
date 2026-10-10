"""Small stateless helpers shared across srcscore_core modules."""

from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone

__all__ = ["now_year_frac", "age_years", "date_age_years", "norm_host", "host_path", "host_matches", "human"]


def now_year_frac() -> float:
    n = datetime.now(timezone.utc)
    return n.year + (n.timetuple().tm_yday / 365.25)


def age_years(date_str, year) -> float:
    """Prefer a full publication_date; fall back to the year; else assume 3y."""
    if date_str:
        try:
            d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
            return max(0.0, now_year_frac() - (d.year + d.timetuple().tm_yday / 365.25))
        except ValueError:
            pass
    if year:
        try:
            return max(0.0, now_year_frac() - float(year))
        except (TypeError, ValueError):
            pass
    return 3.0


def date_age_years(date_str):
    """Known ISO date age in UTC; invalid and future dates are not evidence."""
    if not isinstance(date_str, str) or not date_str.strip():
        return None
    try:
        d = datetime.fromisoformat(date_str.strip().replace("Z", "+00:00"))
        d = d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
        seconds = (datetime.now(timezone.utc) - d).total_seconds()
        return seconds / (365.25 * 86400) if seconds >= 0 else None
    except (ValueError, OverflowError):
        return None


def norm_host(url: str) -> str:
    try:
        p = urllib.parse.urlsplit(url if "//" in url else "https://" + url)
    except ValueError:
        return ""
    h = (p.netloc or "").lower()
    if "@" in h:
        h = h.split("@", 1)[1]
    h = h.split(":", 1)[0]
    return h[4:] if h.startswith("www.") else h


def host_path(url: str) -> str:
    try:
        p = urllib.parse.urlsplit(url if "//" in url else "https://" + url)
    except ValueError:
        return ""
    return norm_host(url) + (p.path or "")


def host_matches(host: str, pattern: str) -> bool:
    """A pattern matches a host itself or any of its subdomains."""
    return host == pattern or host.endswith("." + pattern)


def human(n) -> str:
    n = int(n or 0)
    if n >= 1000000:
        return "%.1fM" % (n / 1e6)
    if n >= 1000:
        return "%.1fk" % (n / 1000)
    return str(n)
