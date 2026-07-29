# asorank

Check where an iOS app ranks in App Store search, for any keyword, from the command line.

No API key. No account. No dependencies. One file, standard library only.

```
$ python3 asorank.py --id 6768664921 --terms "screen time detox,app blocker,digital detox,focus timer"
SproutGuard: Screen Time Detox - 2.3.1 - 5.0* (1 ratings)

keyword            rank
-----------------  ----
screen time detox  #24
app blocker        >50
digital detox      >50
focus timer        >50

1/4 keywords ranking inside the top 50.
```

And `--audit` answers the follow-up question — *can I even win these?*

```
$ python3 asorank.py --id 6768664921 --terms "stop scrolling,app blocker" --audit

keyword         rank  top10-med  open  you  verdict
--------------  ----  --------  ----  ---  -------
stop scrolling  >50        351     2   0%  winnable-metadata
app blocker     >50     11,071     0   0%  entrenched
```

Two apps with **under 10 ratings** are holding top-10 slots for `stop scrolling` purely
because the words are in their titles. Nothing is defending that keyword. `app blocker`
is a wall. Same `>50` rank, opposite conclusions.

## Why

Most ASO tools charge $30-100/month to tell you this. It's a public, unauthenticated
Apple endpoint. The paid part of ASO is historical tracking and volume estimates — the
"where do I actually rank right now" part costs nothing, and it's the part you need
*before* you spend three weeks building.

If you're pre-launch or just shipped, run this against your keyword list. If everything
says `>50`, no amount of copy tuning will help: nobody can see your app at all.

## Install

```sh
git clone https://github.com/shantj/asorank
cd asorank
python3 asorank.py --help
```

Python 3.8+. Nothing to install.

## Usage

```sh
# by App Store numeric ID (exact match — preferred)
python3 asorank.py --id 6768664921 --terms "app blocker,screen time"

# by name (case-insensitive substring — convenient but fuzzy)
python3 asorank.py --app "SproutGuard" --terms "app blocker"

# keyword list from a file, other storefront, deeper than top 50, JSON out
python3 asorank.py --id 6768664921 --terms-file keywords.txt --country gb --limit 200 --json

# is a keyword even winnable? (see below)
python3 asorank.py --id 6768664921 --terms-file keywords.txt --audit
```

| Flag | Meaning |
|---|---|
| `--id` | App Store numeric ID. Exact match. Use this. |
| `--app` | App name, case-insensitive substring match. |
| `--terms` | Comma-separated keywords. |
| `--terms-file` | One keyword per line; blank lines and `#` comments skipped. |
| `--country` | Storefront code, default `us`. |
| `--limit` | Search depth, 1–200 (Apple's ceiling), default 50. |
| `--delay` | Seconds between queries, default 0.4. |
| `--audit` | Judge whether each keyword is winnable, not just where you rank. |
| `--depth` | How many top slots `--audit` inspects, default 10. |
| `--json` | Machine-readable output. |

Exit codes: `0` ok · `2` bad arguments · `3` app not found on that storefront · `4` API error.

## `--audit`: is this keyword even winnable?

Knowing you rank `>50` doesn't tell you what to do about it. There are two very different
reasons to be invisible, and they have opposite responses:

- the top slots are held by apps with **tens of thousands of ratings** — you cannot buy
  your way in with copy changes, so don't try
- the top slots are held by apps with **almost no ratings** that simply have the query
  words in their title — the barrier is metadata, and metadata is free to change

`--audit` tells you which one you're looking at:

```
$ python3 asorank.py --id 6768664921 --terms-file keywords.txt --audit
SproutGuard: Screen Time Detox - 2.3.1 - 5.0* (1 ratings)

keyword                rank  top10-med  open  you  verdict
---------------------  ----  --------  ----  ---  -------
screen time detox      #24      1,402     4  100%  winnable-other
stop scrolling         >50        351     2   0%  winnable-metadata
quit doomscrolling     >50        997     4   0%  winnable-metadata
break phone addiction  >50      2,968     2   0%  winnable-metadata
app blocker            >50     11,071     0   0%  entrenched
block instagram        >50     42,186     0   0%  entrenched

open  = apps in the top 10 with <10 ratings whose title matches the query
you   = share of the query's words present in your app's title
```

**`open` is the column that matters.** Each open slot is an existence proof: an app with
no install base is holding that position right now, so the position is not defended by
authority. `entrenched` keywords have zero of them.

| Verdict | Meaning |
|---|---|
| `winnable-metadata` | Unowned, and your title misses the words. **Best targets.** |
| `winnable-other` | Unowned, but your title already matches — something else is wrong. |
| `ranking` | You're in the top `--depth`. |
| `ranking-deep` | Indexed but below the fold. Not a metadata problem. |
| `contested` | No clear opening, no clear wall. |
| `entrenched` | Held by apps with real install bases. Expensive. |

### Why the `open` column is built the way it is

A low rating count on its own means nothing — plenty of dead apps rank nowhere. The slot
only counts as open if the incumbent is **both** low-authority **and** title-matching,
because that combination is what isolates the variable: it shows the title is what put
it there.

Across 380 top-10 slots sampled over 38 keywords, title-matching apps had a median of
**2,360 ratings** against **10,613** for non-matching ones — a ~4x gap in the opposite
direction from what an authority-driven ranking would produce. 8% of all top-10 slots
were held by apps with under 10 ratings.

This is a heuristic over a public endpoint, not a model of Apple's ranker. It won't tell
you search volume — a wide-open keyword nobody searches for is still worthless, and
`--audit` cannot see that. Use it to rule keywords **out** cheaply, then judge demand
separately.

## Use as a library

```python
from asorank import rank_for, rank_all, lookup_app, audit_term, title_match

rank_for("app blocker", track_id=6768664921)
# {'term': 'app blocker', 'rank': None, 'found': False,
#  'results_seen': 50, 'matched_name': None}

audit_term("stop scrolling", track_id=6768664921,
           my_title="SproutGuard: Screen Time Detox")
# {'term': 'stop scrolling', 'rank': None, 'found': False, ...
#  'top_median_ratings': 351, 'no_authority_slots': 2,
#  'you_title_match': 0.0, 'verdict': 'winnable-metadata'}
```

Pass `my_title` to `audit_term` when using `track_id`. If the app is outside the top
`limit` there's no result row to read your title from, and without it the title match
reads as unknown for exactly the keywords you're auditing. It returns `None` rather
than a fake `0.0`, because a fake zero would wrongly promote every open keyword to
`winnable-metadata`.

`rank` is `None` when not found — never `0` — so `if result["rank"]:` can't accidentally
treat "ranked #1" and "not ranked" the same way. Check `found` if you want to be explicit.

## Three things that will bite you

These cost me real time; they're why this repo exists rather than a gist.

**1. An App Store URL returning HTTP 200 does not mean the app exists.**
Apple redirects unknown slugs to a generic page and serves 200. Validating links by
status code silently passes dead links. Use `lookup_app(id)` — it returns `None` when
the app genuinely isn't on that storefront.

**2. Apps aren't on every storefront.** An app live on `us` can be absent from `de`.
If everything comes back `>50`, check `--country` before concluding you're invisible.

**3. Apple rate-limits.** Rapid-fire queries get HTTP 403. There's a 0.4s default delay
and exponential-backoff retry on 403/429/5xx. Don't set `--delay 0`.

## Tests

```sh
python3 test_asorank.py          # 46 offline tests, no network
python3 test_asorank.py --live   # + 4 tests against Apple's real API
```

50/50 passing. The offline tests cover the parsing and CLI edges (1-based ranks, `None`
vs `0`, null `trackName`, empty body on HTTP 200, retry/backoff behaviour, every exit
code) plus every `--audit` verdict branch. The live tests confirm the endpoint still
behaves as assumed — worth running occasionally, since this depends on an undocumented
public API that Apple can change.

The audit tests are written as a negative control: each verdict is pinned by a case that
differs from its neighbour in exactly one variable, so that e.g. dropping the title-match
condition from the open-slot rule turns the suite red rather than silently changing what
the numbers mean.

## Caveats

Uses `itunes.apple.com/search`, which is public and undocumented for this purpose. It
correlates well with in-App-Store search but is not guaranteed identical, and results
vary by storefront and over time. Treat it as a strong signal, not ground truth.

## Licence

MIT.

---

Built while debugging why my own app got no downloads. Full write-up of what the data
said: [I analyzed 204 screen-time apps to find out why mine gets zero downloads](https://dev.to/samtj/i-analyzed-204-screen-time-apps-to-find-out-why-mine-gets-zero-downloads-2g80).
The app in the examples is mine — [SproutGuard](https://apps.apple.com/us/app/sproutguard-screen-time-detox/id6768664921?ct=asorank), free — used here because it's
the ranking data I'm allowed to publish honestly, including the bad numbers.
