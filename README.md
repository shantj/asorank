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
| `--json` | Machine-readable output. |

Exit codes: `0` ok · `2` bad arguments · `3` app not found on that storefront · `4` API error.

## Use as a library

```python
from asorank import rank_for, rank_all, lookup_app

rank_for("app blocker", track_id=6768664921)
# {'term': 'app blocker', 'rank': None, 'found': False,
#  'results_seen': 50, 'matched_name': None}
```

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
python3 test_asorank.py          # 27 offline tests, no network
python3 test_asorank.py --live   # + 4 tests against Apple's real API
```

31/31 passing. The offline tests cover the parsing and CLI edges (1-based ranks, `None`
vs `0`, null `trackName`, empty body on HTTP 200, retry/backoff behaviour, every exit
code). The live tests confirm the endpoint still behaves as assumed — worth running
occasionally, since this depends on an undocumented public API that Apple can change.

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
