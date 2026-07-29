#!/usr/bin/env python3
"""
asorank - check where an iOS app ranks for App Store search keywords.

No API key, no account, no dependencies. Uses Apple's public iTunes Search API.

Usage:
    python3 asorank.py --app "SproutGuard" --terms "app blocker,screen time detox"
    python3 asorank.py --id 6768664921 --terms-file terms.txt --country gb --json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"
UA = "asorank/1.0 (+https://github.com/shantj/asorank)"
MAX_LIMIT = 200  # Apple's documented ceiling for the search endpoint


class ApiError(RuntimeError):
    pass


def _get(url: str, params: dict, timeout: float = 15.0, retries: int = 3) -> dict:
    """GET JSON with backoff. Apple rate-limits aggressively (HTTP 403)."""
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}", headers={"User-Agent": UA})
    delay = 1.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace").strip()
            # Apple sometimes returns an empty body with HTTP 200.
            return json.loads(body) if body else {"resultCount": 0, "results": []}
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 500, 503) and attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise ApiError(f"HTTP {e.code} from iTunes API") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise ApiError(f"network error: {e}") from e
    raise ApiError("unreachable")


def lookup_app(track_id: int, country: str = "us") -> dict | None:
    """Resolve an App Store numeric ID to its metadata. Returns None if not found.

    Use this rather than trusting an App Store URL: Apple serves HTTP 200 for
    unknown app slugs (it redirects to a generic page), so a 200 does not mean
    the app exists on that storefront.
    """
    data = _get(LOOKUP_URL, {"id": track_id, "country": country, "entity": "software"})
    results = data.get("results") or []
    return results[0] if results else None


def _matches(app: dict, name: str | None, track_id: int | None) -> bool:
    if track_id is not None:
        return app.get("trackId") == track_id
    return (name or "").lower() in (app.get("trackName") or "").lower()


def rank_for(
    term: str,
    *,
    name: str | None = None,
    track_id: int | None = None,
    country: str = "us",
    limit: int = 50,
) -> dict:
    """Return the 1-based rank of an app in the results for `term`.

    Exactly one of `name` or `track_id` must be given. Matching by track_id is
    exact; matching by name is a case-insensitive substring test and can pick up
    a competitor with your word in its title, so prefer the ID.

    Returns {"term", "rank", "found", "results_seen", "matched_name"}.
    `rank` is None when the app is not inside the top `limit`.
    """
    if (name is None) == (track_id is None):
        raise ValueError("pass exactly one of name= or track_id=")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be 1..{MAX_LIMIT}")
    if not term.strip():
        raise ValueError("term must not be empty")

    data = _get(
        SEARCH_URL,
        {
            "term": term,
            "country": country,
            "entity": "software",
            "media": "software",
            "limit": limit,
        },
    )
    results = data.get("results") or []
    for i, app in enumerate(results, 1):
        if _matches(app, name, track_id):
            return {
                "term": term,
                "rank": i,
                "found": True,
                "results_seen": len(results),
                "matched_name": app.get("trackName"),
            }
    return {
        "term": term,
        "rank": None,
        "found": False,
        "results_seen": len(results),
        "matched_name": None,
    }


def rank_all(terms, *, delay: float = 0.4, **kw) -> list:
    out = []
    for i, t in enumerate(terms):
        if i:
            time.sleep(delay)  # be polite; Apple 403s on rapid-fire queries
        out.append(rank_for(t, **kw))
    return out


def _fmt_table(rows: list, limit: int) -> str:
    width = max([len(r["term"]) for r in rows] + [7])
    lines = [f"{'keyword'.ljust(width)}  rank", f"{'-' * width}  ----"]
    for r in rows:
        rank = f"#{r['rank']}" if r["found"] else f">{limit}"
        lines.append(f"{r['term'].ljust(width)}  {rank}")
    found = sum(1 for r in rows if r["found"])
    lines.append("")
    lines.append(f"{found}/{len(rows)} keywords ranking inside the top {limit}.")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="asorank",
        description="Check where an iOS app ranks for App Store search keywords.",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--app", help="app name (case-insensitive substring match)")
    g.add_argument("--id", type=int, help="App Store numeric ID (exact, preferred)")
    t = p.add_mutually_exclusive_group(required=True)
    t.add_argument("--terms", help="comma-separated keywords")
    t.add_argument("--terms-file", help="file with one keyword per line")
    p.add_argument("--country", default="us", help="storefront code (default: us)")
    p.add_argument("--limit", type=int, default=50,
                   help=f"how deep to look, 1..{MAX_LIMIT} (default: 50)")
    p.add_argument("--delay", type=float, default=0.4,
                   help="seconds between queries (default: 0.4)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    a = p.parse_args(argv)

    if a.terms_file:
        try:
            with open(a.terms_file, encoding="utf-8") as fh:
                terms = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
        except OSError as e:
            print(f"error: cannot read {a.terms_file}: {e}", file=sys.stderr)
            return 2
    else:
        terms = [s.strip() for s in a.terms.split(",") if s.strip()]
    if not terms:
        print("error: no keywords given", file=sys.stderr)
        return 2

    try:
        if a.id is not None:
            app = lookup_app(a.id, a.country)
            if app is None:
                print(
                    f"error: app id {a.id} not found on the '{a.country}' storefront.\n"
                    "It may not be published there. Try --country us.",
                    file=sys.stderr,
                )
                return 3
            if not a.json:
                print(f"{app.get('trackName')} - {app.get('version')} - "
                      f"{app.get('averageUserRating', 0):.1f}* "
                      f"({app.get('userRatingCount', 0)} ratings)\n")
        rows = rank_all(
            terms, delay=a.delay, name=a.app, track_id=a.id,
            country=a.country, limit=a.limit,
        )
    except ApiError as e:
        print(f"error: {e}", file=sys.stderr)
        return 4
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if a.json:
        print(json.dumps(rows, indent=2))
    else:
        print(_fmt_table(rows, a.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
