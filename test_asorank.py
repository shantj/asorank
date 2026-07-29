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
        a = asorank.rank_for("screen time detox", track_id=6768664921)
        b = asorank.rank_for("screen time detox", track_id=6768664921)
        self.assertEqual(a["rank"], b["rank"])


if __name__ == "__main__":
    unittest.main(argv=[a for a in sys.argv if a != "--live"], verbosity=2)
