#!/usr/bin/env python3
"""Tests for asorank. Offline by default; add --live to hit Apple's real API."""
import io
import json
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

import asorank


def fake_search(names):
    return {"resultCount": len(names),
            "results": [{"trackName": n, "trackId": 1000 + i}
                        for i, n in enumerate(names)]}


class TestRankFor(unittest.TestCase):
    def test_finds_by_name_1_based(self):
        with mock.patch.object(asorank, "_get",
                               return_value=fake_search(["Opal", "SproutGuard", "Forest"])):
            r = asorank.rank_for("app blocker", name="sproutguard")
        self.assertEqual(r["rank"], 2)          # 1-based, not 0-based
        self.assertTrue(r["found"])
        self.assertEqual(r["matched_name"], "SproutGuard")

    def test_first_position_is_1_not_0(self):
        with mock.patch.object(asorank, "_get", return_value=fake_search(["SproutGuard"])):
            self.assertEqual(asorank.rank_for("x", name="SproutGuard")["rank"], 1)

    def test_not_found_returns_none_not_zero(self):
        with mock.patch.object(asorank, "_get", return_value=fake_search(["Opal"])):
            r = asorank.rank_for("x", name="SproutGuard")
        self.assertIsNone(r["rank"])            # None, so `if rank:` can't confuse rank 1
        self.assertFalse(r["found"])

    def test_empty_results(self):
        with mock.patch.object(asorank, "_get", return_value={"results": []}):
            self.assertFalse(asorank.rank_for("x", name="A")["found"])

    def test_missing_results_key(self):
        with mock.patch.object(asorank, "_get", return_value={}):
            self.assertFalse(asorank.rank_for("x", name="A")["found"])

    def test_null_trackname_does_not_crash(self):
        with mock.patch.object(asorank, "_get",
                               return_value={"results": [{"trackName": None}, {"trackName": "A"}]}):
            self.assertEqual(asorank.rank_for("x", name="a")["rank"], 2)

    def test_name_match_is_case_insensitive(self):
        with mock.patch.object(asorank, "_get", return_value=fake_search(["SPROUTGUARD"])):
            self.assertTrue(asorank.rank_for("x", name="sproutguard")["found"])

    def test_id_match_is_exact_not_substring(self):
        res = {"results": [{"trackName": "Other", "trackId": 6768664921},
                           {"trackName": "SproutGuard", "trackId": 999}]}
        with mock.patch.object(asorank, "_get", return_value=res):
            self.assertEqual(asorank.rank_for("x", track_id=6768664921)["rank"], 1)

    def test_name_match_can_catch_a_competitor(self):
        """Documents why --id is preferred: substring matching is fuzzy."""
        with mock.patch.object(asorank, "_get",
                               return_value=fake_search(["Focus Buddy Pro", "Focus"])):
            self.assertEqual(asorank.rank_for("x", name="Focus")["matched_name"],
                             "Focus Buddy Pro")

    def test_requires_exactly_one_selector(self):
        for kw in ({}, {"name": "A", "track_id": 1}):
            with self.assertRaises(ValueError):
                asorank.rank_for("x", **kw)

    def test_rejects_bad_limit(self):
        for lim in (0, -1, 201):
            with self.assertRaises(ValueError):
                asorank.rank_for("x", name="A", limit=lim)

    def test_rejects_blank_term(self):
        with self.assertRaises(ValueError):
            asorank.rank_for("   ", name="A")

    def test_limit_is_passed_through_to_api(self):
        with mock.patch.object(asorank, "_get", return_value=fake_search([])) as g:
            asorank.rank_for("t", name="A", limit=200, country="gb")
        self.assertEqual(g.call_args[0][1]["limit"], 200)
        self.assertEqual(g.call_args[0][1]["country"], "gb")


class TestHttp(unittest.TestCase):
    def test_empty_body_200_is_not_a_crash(self):
        """Apple really does return HTTP 200 with a blank body sometimes."""
        class R:
            def read(self): return b"   "
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with mock.patch("urllib.request.urlopen", return_value=R()):
            self.assertEqual(asorank._get("https://x", {}), {"resultCount": 0, "results": []})

    def test_retries_then_succeeds(self):
        import urllib.error
        ok = mock.MagicMock()
        ok.__enter__.return_value.read.return_value = b'{"results":[]}'
        err = urllib.error.HTTPError("u", 403, "Forbidden", None, None)
        with mock.patch("urllib.request.urlopen", side_effect=[err, ok]), \
             mock.patch("time.sleep"):
            self.assertEqual(asorank._get("https://x", {}, retries=3), {"results": []})

    def test_gives_up_and_raises_apierror(self):
        import urllib.error
        err = urllib.error.HTTPError("u", 403, "Forbidden", None, None)
        with mock.patch("urllib.request.urlopen", side_effect=err), \
             mock.patch("time.sleep"):
            with self.assertRaises(asorank.ApiError):
                asorank._get("https://x", {}, retries=2)

    def test_404_is_not_retried(self):
        import urllib.error
        err = urllib.error.HTTPError("u", 404, "NF", None, None)
        with mock.patch("urllib.request.urlopen", side_effect=err) as u, \
             mock.patch("time.sleep"):
            with self.assertRaises(asorank.ApiError):
                asorank._get("https://x", {}, retries=3)
        self.assertEqual(u.call_count, 1)


class TestLookup(unittest.TestCase):
    def test_returns_none_when_absent(self):
        with mock.patch.object(asorank, "_get", return_value={"results": []}):
            self.assertIsNone(asorank.lookup_app(1))

    def test_returns_first_result(self):
        with mock.patch.object(asorank, "_get",
                               return_value={"results": [{"trackName": "S"}]}):
            self.assertEqual(asorank.lookup_app(1)["trackName"], "S")


class TestCli(unittest.TestCase):
    def run_cli(self, argv, results=None):
        buf, err = io.StringIO(), io.StringIO()
        with mock.patch.object(asorank, "rank_all", return_value=results or []), \
             mock.patch.object(asorank, "lookup_app",
                               return_value={"trackName": "SproutGuard", "version": "2.3.1",
                                             "averageUserRating": 5.0, "userRatingCount": 1}):
            with redirect_stdout(buf), redirect_stderr(err):
                code = asorank.main(argv)
        return code, buf.getvalue(), err.getvalue()

    def test_json_output_is_valid_json(self):
        rows = [{"term": "a", "rank": 3, "found": True,
                 "results_seen": 50, "matched_name": "S"}]
        code, out, _ = self.run_cli(["--id", "1", "--terms", "a", "--json"], rows)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)[0]["rank"], 3)

    def test_table_shows_gt_limit_for_missing(self):
        rows = [{"term": "app blocker", "rank": None, "found": False,
                 "results_seen": 50, "matched_name": None}]
        code, out, _ = self.run_cli(["--app", "S", "--terms", "app blocker"], rows)
        self.assertEqual(code, 0)
        self.assertIn(">50", out)
        self.assertIn("0/1 keywords", out)

    def test_unknown_id_exits_3_and_says_why(self):
        buf, err = io.StringIO(), io.StringIO()
        with mock.patch.object(asorank, "lookup_app", return_value=None):
            with redirect_stdout(buf), redirect_stderr(err):
                code = asorank.main(["--id", "1", "--terms", "a"])
        self.assertEqual(code, 3)
        self.assertIn("not found", err.getvalue())

    def test_api_error_exits_4(self):
        buf, err = io.StringIO(), io.StringIO()
        with mock.patch.object(asorank, "lookup_app", return_value={"trackName": "S"}), \
             mock.patch.object(asorank, "rank_all",
                               side_effect=asorank.ApiError("HTTP 403 from iTunes API")):
            with redirect_stdout(buf), redirect_stderr(err):
                code = asorank.main(["--id", "1", "--terms", "a"])
        self.assertEqual(code, 4)
        self.assertIn("403", err.getvalue())

    def test_empty_terms_exits_2(self):
        code, _, err = self.run_cli(["--app", "S", "--terms", " , , "])
        self.assertEqual(code, 2)

    def test_missing_terms_file_exits_2(self):
        code, _, err = self.run_cli(["--app", "S", "--terms-file", "/no/such/file"])
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)

    def test_app_and_id_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            asorank.main(["--app", "S", "--id", "1", "--terms", "a"])

    def test_terms_file_is_read_and_comments_skipped(self):
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as fh:
            fh.write("app blocker\n# a comment\n\nscreen time\n")
        try:
            captured = {}
            def fake(terms, **kw):
                captured["terms"] = terms
                return []
            with mock.patch.object(asorank, "rank_all", side_effect=fake), \
                 redirect_stdout(io.StringIO()):
                asorank.main(["--app", "S", "--terms-file", path])
        finally:
            os.unlink(path)
        self.assertEqual(captured["terms"], ["app blocker", "screen time"])


def fake_results(rows):
    """rows: list of (trackName, userRatingCount)."""
    return {"resultCount": len(rows),
            "results": [{"trackName": n, "trackId": 1000 + i, "userRatingCount": c}
                        for i, (n, c) in enumerate(rows)]}


class TestTokenize(unittest.TestCase):
    def test_drops_stopwords_and_short_fragments(self):
        self.assertEqual(asorank.tokenize("Block the Apps for My Phone"),
                         ["block", "phone"])

    def test_handles_none_and_empty(self):
        self.assertEqual(asorank.tokenize(None), [])
        self.assertEqual(asorank.tokenize(""), [])

    def test_punctuation_and_case(self):
        self.assertEqual(asorank.tokenize("Kael: Quit Doomscrolling."),
                         ["kael", "quit", "doomscrolling"])


class TestTitleMatch(unittest.TestCase):
    def test_full_and_partial_and_zero(self):
        self.assertEqual(asorank.title_match("screen time detox",
                                             "SproutGuard: Screen Time Detox"), 1.0)
        self.assertEqual(asorank.title_match("stop scrolling",
                                             "DoomSafe: Stop Scrolling"), 1.0)
        self.assertEqual(asorank.title_match("stop scrolling",
                                             "SproutGuard: Screen Time Detox"), 0.0)

    def test_partial_is_fractional(self):
        # "scrolling detox": 1 of 2 tokens present
        self.assertAlmostEqual(
            asorank.title_match("scrolling detox", "SproutGuard: Screen Time Detox"), 0.5)

    def test_all_stopword_query_does_not_divide_by_zero(self):
        self.assertEqual(asorank.title_match("the app", "Anything"), 0.0)


class TestAuditTerm(unittest.TestCase):
    """The audit's whole value is its verdict. Each branch is pinned."""

    def _audit(self, rows, term, my_title, **kw):
        # rank_for and the audit's own fetch both go through _get.
        with mock.patch.object(asorank, "_get", return_value=fake_results(rows)):
            return asorank.audit_term(term, track_id=999999,
                                      my_title=my_title, **kw)

    def test_winnable_metadata_when_weak_app_holds_slot_and_title_misses(self):
        r = self._audit([("DoomSafe: Stop Scrolling", 1)] + [("Big App", 50000)] * 9,
                        "stop scrolling", "SproutGuard: Screen Time Detox")
        self.assertEqual(r["verdict"], "winnable-metadata")
        self.assertEqual(r["no_authority_slots"], 1)
        self.assertEqual(r["you_title_match"], 0.0)

    def test_high_rating_title_match_is_not_an_open_slot(self):
        # Same title match, but the holder has real authority -> not "open".
        r = self._audit([("DoomSafe: Stop Scrolling", 90000)] + [("Big App", 50000)] * 9,
                        "stop scrolling", "SproutGuard: Screen Time Detox")
        self.assertEqual(r["no_authority_slots"], 0)
        self.assertEqual(r["verdict"], "entrenched")

    def test_weak_app_that_does_not_title_match_is_not_an_open_slot(self):
        # Low ratings alone is not evidence; it must also match the query.
        r = self._audit([("Unrelated Puzzle Game", 2)] + [("Mid App", 500)] * 9,
                        "stop scrolling", "SproutGuard: Screen Time Detox")
        self.assertEqual(r["no_authority_slots"], 0)
        self.assertEqual(r["verdict"], "contested")

    def test_winnable_other_when_your_title_already_matches(self):
        r = self._audit([("Tiny Detox", 1)] + [("Mid App", 500)] * 9,
                        "detox", "SproutGuard: Screen Time Detox")
        self.assertEqual(r["verdict"], "winnable-other")

    def test_ranking_when_inside_depth(self):
        rows = [("SproutGuard: Screen Time Detox", 1)] + [("Tiny Detox", 1)] * 9
        with mock.patch.object(asorank, "_get", return_value=fake_results(rows)):
            r = asorank.audit_term("detox", track_id=1000,
                                   my_title="SproutGuard: Screen Time Detox")
        self.assertEqual(r["rank"], 1)
        self.assertEqual(r["verdict"], "ranking")

    def test_ranking_deep_is_not_reported_as_a_metadata_opportunity(self):
        """Found but below depth: metadata is not the blocker, so must not be
        labelled winnable-metadata even though open slots exist."""
        rows = ([("DoomSafe: Stop Scrolling", 1)] + [("Filler", 5000)] * 18
                + [("SproutGuard: Screen Time Detox", 1)])
        with mock.patch.object(asorank, "_get", return_value=fake_results(rows)):
            r = asorank.audit_term("stop scrolling", track_id=1019,
                                   my_title="SproutGuard: Screen Time Detox",
                                   depth=10)
        self.assertEqual(r["rank"], 20)
        self.assertEqual(r["verdict"], "ranking-deep")

    def test_unknown_title_yields_none_not_a_fake_zero(self):
        """Without a title, a 0% match would wrongly promote every open keyword."""
        r = self._audit([("DoomSafe: Stop Scrolling", 1)] + [("Big", 50000)] * 9,
                        "stop scrolling", None)
        self.assertIsNone(r["you_title_match"])
        self.assertNotEqual(r["verdict"], "winnable-metadata")
        self.assertEqual(r["verdict"], "winnable-other")

    def test_depth_is_respected(self):
        rows = [("Big", 90000)] * 3 + [("DoomSafe: Stop Scrolling", 1)] * 1 \
            + [("Big", 90000)] * 6
        shallow = self._audit(rows, "stop scrolling", "X App", depth=3)
        deep = self._audit(rows, "stop scrolling", "X App", depth=10)
        self.assertEqual(shallow["no_authority_slots"], 0)
        self.assertEqual(deep["no_authority_slots"], 1)

    def test_rejects_both_or_neither_identifier(self):
        with self.assertRaises(ValueError):
            asorank.audit_term("x", track_id=1, name="y")
        with self.assertRaises(ValueError):
            asorank.audit_term("x")

    def test_rejects_bad_depth(self):
        with self.assertRaises(ValueError):
            asorank.audit_term("x", track_id=1, depth=0)

    def test_missing_rating_field_defaults_to_zero_not_crash(self):
        payload = {"resultCount": 1,
                   "results": [{"trackName": "Stop Scrolling", "trackId": 5}]}
        with mock.patch.object(asorank, "_get", return_value=payload):
            r = asorank.audit_term("stop scrolling", track_id=999,
                                   my_title="SproutGuard")
        self.assertEqual(r["no_authority_slots"], 1)


class TestAuditFormatting(unittest.TestCase):
    def test_unknown_title_renders_as_question_mark_not_zero_percent(self):
        rows = [{"term": "x", "rank": None, "found": False, "results_seen": 50,
                 "top_median_ratings": 100, "top_min_ratings": 0,
                 "no_authority_slots": 1, "no_authority_examples": [],
                 "supply": 3, "pool": 50, "supply_share": 0.06,
                 "you_title_match": None, "verdict": "winnable-other"}]
        out = asorank._fmt_audit(rows, 10)
        self.assertIn("?", out)
        self.assertNotIn("0%", out)

    def test_every_verdict_has_a_note(self):
        produced = {"ranking", "ranking-deep", "winnable-metadata",
                    "winnable-other", "entrenched", "contested", "crowded"}
        self.assertEqual(produced, set(asorank.VERDICT_NOTE))


class TestSupply(unittest.TestCase):
    """Supply = how many apps already own the query's words in their titles.

    Round-12 finding: rank alone cannot distinguish 'nobody is here' from
    'everybody is here', and those demand opposite actions.
    """

    @staticmethod
    def _payload(matching, nonmatching, ratings=50_000):
        res = [{"trackName": f"Brain Rot Blocker {i}", "trackId": 100 + i,
                "userRatingCount": ratings} for i in range(matching)]
        res += [{"trackName": f"Unrelated Thing {i}", "trackId": 900 + i,
                 "userRatingCount": ratings} for i in range(nonmatching)]
        return {"resultCount": len(res), "results": res}

    def test_supply_counts_full_pool_not_just_top_depth(self):
        # 30 title-matchers but only 10 fit in `depth` - supply must see all 30,
        # otherwise it is capped by depth and can never exceed it.
        payload = self._payload(30, 20)
        with mock.patch.object(asorank, "_get", return_value=payload):
            r = asorank.audit_term("brain rot", track_id=999, depth=10,
                                   my_title="SproutGuard")
        self.assertEqual(r["supply"], 30)
        self.assertEqual(r["pool"], 50)
        self.assertEqual(r["supply_share"], 0.6)

    def test_crowded_when_most_rivals_already_title_match(self):
        payload = self._payload(45, 5)
        with mock.patch.object(asorank, "_get", return_value=payload):
            r = asorank.audit_term("brain rot", track_id=999, depth=10,
                                   my_title="SproutGuard")
        self.assertEqual(r["verdict"], "crowded")

    def test_open_field_is_not_crowded(self):
        payload = self._payload(3, 47)
        with mock.patch.object(asorank, "_get", return_value=payload):
            r = asorank.audit_term("brain rot", track_id=999, depth=10,
                                   my_title="SproutGuard")
        self.assertNotEqual(r["verdict"], "crowded")

    def test_crowded_beats_winnable_metadata(self):
        """The false positive this guard exists to stop.

        A crowded keyword can still show a zero-rating app in the top 10. Without
        the ordering, that lone weak slot reads as 'winnable-metadata' and tells
        you to chase a keyword 90% of the field already owns.
        """
        payload = self._payload(45, 5)
        payload["results"][0]["userRatingCount"] = 0  # a weak slot at #1
        with mock.patch.object(asorank, "_get", return_value=payload):
            r = asorank.audit_term("brain rot", track_id=999, depth=10,
                                   my_title="SproutGuard")
        self.assertGreaterEqual(r["no_authority_slots"], 1)  # weak slot IS there
        self.assertEqual(r["verdict"], "crowded")            # but does not win

    def test_empty_pool_does_not_divide_by_zero(self):
        with mock.patch.object(asorank, "_get",
                               return_value={"resultCount": 0, "results": []}):
            r = asorank.audit_term("nonsense qqq", track_id=999,
                                   my_title="SproutGuard")
        self.assertEqual(r["supply"], 0)
        self.assertEqual(r["supply_share"], 0.0)

    def test_ranking_verdict_not_overridden_by_crowded(self):
        """If you already rank, the field being crowded is irrelevant."""
        payload = self._payload(45, 5)
        payload["results"][0]["trackId"] = 999  # that's us, at #1
        with mock.patch.object(asorank, "_get", return_value=payload):
            r = asorank.audit_term("brain rot", track_id=999, depth=10,
                                   my_title="SproutGuard")
        self.assertEqual(r["verdict"], "ranking")


class TestLive(unittest.TestCase):
    """Real network. Skipped unless run with --live."""
    def setUp(self):
        if "--live" not in sys.argv:
            self.skipTest("pass --live to run network tests")

    def test_lookup_real_app(self):
        app = asorank.lookup_app(6768664921, "us")
        self.assertIsNotNone(app)
        self.assertIn("SproutGuard", app["trackName"])

    def test_lookup_unknown_id_is_none(self):
        self.assertIsNone(asorank.lookup_app(1, "us"))

    def test_rank_real_keyword(self):
        r = asorank.rank_for("screen time detox", track_id=6768664921, limit=50)
        print(f"\n   [live] 'screen time detox' -> "
              f"{'#' + str(r['rank']) if r['found'] else '>50'} "
              f"(saw {r['results_seen']} results)")
        self.assertTrue(1 <= r["rank"] <= 50 if r["found"] else True)

    def test_ranks_are_stable_across_two_calls(self):
        """Consecutive calls agree to within a slot or two.

        Deliberately NOT assertEqual: Apple's ranking genuinely oscillates
        between adjacent positions minute to minute (observed #22-#25 for this
        keyword in a single day), so exact equality made this test fail on a
        working endpoint. What's worth pinning is that the endpoint isn't
        returning noise - agreement on found-ness, and a rank that doesn't jump.
        """
        a = asorank.rank_for("screen time detox", track_id=6768664921)
        b = asorank.rank_for("screen time detox", track_id=6768664921)
        self.assertEqual(a["found"], b["found"])
        if a["found"]:
            self.assertLessEqual(abs(a["rank"] - b["rank"]), 3)


if __name__ == "__main__":
    unittest.main(argv=[a for a in sys.argv if a != "--live"], verbosity=2)
