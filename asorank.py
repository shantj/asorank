#!/usr/bin/env python3
"""
asorank - check where an iOS app ranks for App Store search keywords.

No API key, no account, no dependencies. Uses Apple's public iTunes Search API.

Usage:
    python3 asorank.py --app "SproutGuard" --terms "app blocker,screen time detox"
    python3 asorank.py --id 6768664921 --terms-file terms.txt --country gb --json
    python3 asorank.py --id 6768664921 --terms-file terms.txt --audit
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"
UA = "asorank/1.0 (+https://github.com/shantj/asorank)"
MAX_LIMIT = 200  # Apple's documented ceiling for the search endpoint

# Words too generic to carry ranking weight in a title match test.
STOPWORDS = frozenset(
    "a an the for to of my your app apps on in no and or is it with".split()
)
# An app with fewer ratings than this has essentially no authority signal.
NO_AUTHORITY_RATINGS = 10
# Share of query tokens that must appear in a title to count as a title match.
TITLE_MATCH_THRESHOLD = 0.5
# Share of the result pool already title-matching the query, above which adding
# the words to your own title just makes you one more identical matcher and the
# tiebreak falls back to install base. Calibrated against live measurements:
# 'screen' 155/182 (85%), 'time' 148/170 (87%), 'scroll' 124/176 (70%) and
# 'detox' 107/193 (55%) are all keywords a 1-rating app cannot enter, while
# 'doomscroll' 28/167 (17%) and 'brain rot' 5/172 (3%) have live top-10 slots
# held by 0-rating apps. 0.5 sits in the empty gap between those two clusters.
CROWDED_SUPPLY_SHARE = 0.5


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


def tokenize(text: str | None) -> list:
    """Lowercase word tokens, minus stopwords and 1-2 letter fragments."""
    return [
        t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
        if t not in STOPWORDS and len(t) > 2
    ]


def title_match(term: str, name: str) -> float:
    """Share of the query's meaningful tokens that appear in an app's title.

    Returns 0.0..1.0. A term made entirely of stopwords returns 0.0 rather than
    dividing by zero.
    """
    qt = tokenize(term)
    if not qt:
        return 0.0
    nt = set(tokenize(name))
    return sum(1 for t in qt if t in nt) / len(qt)


def audit_term(
    term: str,
    *,
    track_id: int | None = None,
    name: str | None = None,
    country: str = "us",
    limit: int = 50,
    depth: int = 10,
    my_title: str | None = None,
) -> dict:
    """Assess whether a keyword is *winnable*, not just where you currently rank.

    Rank alone can't tell you if you're losing to entrenched incumbents or simply
    absent from the index. This inspects who actually holds the top `depth` slots:

      top_median_ratings  - how much authority the leaders really have
      no_authority_slots  - top-`depth` apps with <10 ratings that title-match the
                            query. Each one is proof that an app with no install
                            base can hold that slot, so the barrier is metadata.
      you_title_match     - whether YOUR title carries the query's tokens
      verdict             - what to do about it

    A keyword with >=1 no_authority_slot and a low title match on your side is the
    best kind of target: nothing is defending it and the fix is in your control.

    `my_title` is your app's real App Store title. Pass it when using track_id: if
    the app is outside the top `limit` there is no result row to read the name
    from, and without it the title match silently reads 0% for exactly the
    keywords the audit exists to judge.
    """
    if (name is None) == (track_id is None):
        raise ValueError("pass exactly one of name= or track_id=")
    if depth < 1:
        raise ValueError("depth must be >= 1")

    res = rank_for(term, name=name, track_id=track_id,
                   country=country, limit=limit)
    data = _get(
        SEARCH_URL,
        {"term": term, "country": country, "entity": "software",
         "media": "software", "limit": limit},
    )
    results = (data.get("results") or [])[:depth]

    ratings = [(a.get("userRatingCount") or 0) for a in results]

    # Supply: how many apps in the whole result pool carry the query's tokens in
    # their title. This is the discriminator rank alone hides. Two keywords can
    # both show you at >50 for opposite reasons: 150 rivals already own the words
    # (you are outnumbered, metadata will not save you), or 5 do (the field is
    # empty and the words are simply missing from your title). Measured on the
    # full pool, not the top `depth`, because the depth slice is the *outcome* of
    # the competition and cannot measure its size.
    all_results = data.get("results") or []
    supply = sum(
        1 for a in all_results
        if title_match(term, a.get("trackName") or "") >= TITLE_MATCH_THRESHOLD
    )
    pool = len(all_results)
    supply_share = (supply / pool) if pool else 0.0

    weak = [
        {
            "pos": i,
            "name": a.get("trackName") or "",
            "ratings": a.get("userRatingCount") or 0,
        }
        for i, a in enumerate(results, 1)
        if (a.get("userRatingCount") or 0) < NO_AUTHORITY_RATINGS
        and title_match(term, a.get("trackName") or "") >= TITLE_MATCH_THRESHOLD
    ]

    mine = my_title or res.get("matched_name") or name or ""
    my_match = title_match(term, mine) if mine else 0.0

    if not mine:
        # No title to compare against - do not fabricate a 0% match, which would
        # wrongly promote every open keyword to "winnable-metadata".
        my_match = None

    if res["found"] and res["rank"] is not None and res["rank"] <= depth:
        verdict = "ranking"
    elif res["found"]:
        # Indexed and visible, just below the fold. Metadata is not the blocker
        # here, so it must not be reported as a metadata opportunity.
        verdict = "ranking-deep"
    elif supply_share >= CROWDED_SUPPLY_SHARE:
        # The words are already in most rivals' titles. Adding them to yours makes
        # you one of N identical matchers and the tiebreak reverts to authority,
        # which is the thing you do not have. Checked BEFORE the weak-slot test:
        # a crowded keyword can still show a no-authority slot, and calling that
        # "winnable-metadata" is the exact false positive this guard exists to stop.
        verdict = "crowded"
    elif weak and (my_match is not None and my_match < TITLE_MATCH_THRESHOLD):
        verdict = "winnable-metadata"
    elif weak:
        verdict = "winnable-other"
    elif ratings and statistics.median(ratings) >= 10_000:
        verdict = "entrenched"
    else:
        verdict = "contested"

    return {
        "term": term,
        "rank": res["rank"],
        "found": res["found"],
        "results_seen": res["results_seen"],
        "top_median_ratings": int(statistics.median(ratings)) if ratings else 0,
        "top_min_ratings": min(ratings) if ratings else 0,
        "no_authority_slots": len(weak),
        "no_authority_examples": weak[:3],
        "supply": supply,
        "pool": pool,
        "supply_share": round(supply_share, 2),
        "you_title_match": None if my_match is None else round(my_match, 2),
        "verdict": verdict,
    }


def audit_all(terms, *, delay: float = 0.4, **kw) -> list:
    out = []
    for i, t in enumerate(terms):
        if i:
            time.sleep(delay)
        out.append(audit_term(t, **kw))
    return out


VERDICT_NOTE = {
    "ranking": "already in the top slots",
    "ranking-deep": "indexed but below the fold - not a metadata problem",
    "winnable-metadata": "unowned AND your title misses the words - best targets",
    "winnable-other": "unowned, but your title already matches - look elsewhere",
    "crowded": "most rivals already title-match - metadata won't break in",
    "entrenched": "held by apps with real install bases - expensive",
    "contested": "no clear opening, no clear wall",
}


def _fmt_audit(rows: list, depth: int) -> str:
    width = max([len(r["term"]) for r in rows] + [7])
    out = [
        f"{'keyword'.ljust(width)}  rank  top{depth}-med  supply  open  you  verdict",
        f"{'-' * width}  ----  --------  ------  ----  ---  -------",
    ]
    for r in rows:
        rank = f"#{r['rank']}" if r["found"] else ">50"
        you = "  ?" if r["you_title_match"] is None else f"{r['you_title_match']:>3.0%}"
        sup = f"{r['supply']:>3}/{r['pool']:<3}"
        out.append(
            f"{r['term'].ljust(width)}  {rank:<4}  {r['top_median_ratings']:>8,}  "
            f"{sup}  {r['no_authority_slots']:>4}  {you}  {r['verdict']}"
        )

    order = ["winnable-metadata", "winnable-other", "ranking", "ranking-deep",
             "contested", "crowded", "entrenched"]
    out.append("")
    out.append(f"supply= apps in the result pool whose title already matches the query")
    out.append(f"open  = apps in the top {depth} with <{NO_AUTHORITY_RATINGS} ratings whose "
               "title matches the query")
    out.append("you   = share of the query's words present in your app's title")
    out.append("")
    for v in order:
        hits = [r for r in rows if r["verdict"] == v]
        if hits:
            out.append(f"{len(hits):>3}  {v:<18} {VERDICT_NOTE[v]}")

    best = [r for r in rows if r["verdict"] == "winnable-metadata"]
    if best:
        best.sort(key=lambda r: (-r["no_authority_slots"], r["top_median_ratings"]))
        out.append("")
        out.append("Start here - nothing with an install base is defending these,")
        out.append("and the words are missing from your title:")
        for r in best[:8]:
            ex = r["no_authority_examples"][0] if r["no_authority_examples"] else None
            tail = (f"  (e.g. #{ex['pos']} '{ex['name'][:34]}' at "
                    f"{ex['ratings']} ratings)") if ex else ""
            out.append(f"  - {r['term']}{tail}")
    return "\n".join(out)


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
    p.add_argument("--audit", action="store_true",
                   help="also judge whether each keyword is winnable, not just your rank")
    p.add_argument("--depth", type=int, default=10,
                   help="how many top slots --audit inspects (default: 10)")
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
        my_title = None
        if a.id is not None:
            app = lookup_app(a.id, a.country)
            if app is None:
                print(
                    f"error: app id {a.id} not found on the '{a.country}' storefront.\n"
                    "It may not be published there. Try --country us.",
                    file=sys.stderr,
                )
                return 3
            my_title = app.get("trackName")
            if not a.json:
                print(f"{app.get('trackName')} - {app.get('version')} - "
                      f"{app.get('averageUserRating', 0):.1f}* "
                      f"({app.get('userRatingCount', 0)} ratings)\n")
        rows = (
            audit_all(terms, delay=a.delay, name=a.app, track_id=a.id,
                      country=a.country, limit=a.limit, depth=a.depth,
                      my_title=my_title or a.app)
            if a.audit else
            rank_all(terms, delay=a.delay, name=a.app, track_id=a.id,
                     country=a.country, limit=a.limit)
        )
    except ApiError as e:
        print(f"error: {e}", file=sys.stderr)
        return 4
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if a.json:
        print(json.dumps(rows, indent=2))
    elif a.audit:
        print(_fmt_audit(rows, a.depth))
    else:
        print(_fmt_table(rows, a.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
