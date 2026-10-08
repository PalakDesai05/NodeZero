#!/usr/bin/env python3
"""Acceptance Test Suite for Search Reliability and Graph Features (R6).
Tests:
- R1 Input Validation: Hard-invalid (HTTP 422 for '', '...', 'aaaaa'),
  accepted short acronyms ('UK', 'VPN', 'ODI'), soft-gibberish ('xyz..').
- R2 & R5 Search Reliability: Real queries ('odi cricket', '100 runs in odi',
  'century odi', '#odi', 'vpn', 'nepal flood', 'vaccine', 'mueller report', 'buttigieg')
  with per-source thread counts and diagnostics.
- Mocked HTTP tests for planner, adaptive thresholds, session refresh, and retries.
- Graph & User Panel: Parent->child links, username, date/time, location ('Not available').
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from main import app
import search_validator as sv
import query_planner as qp
import detector
import live
import store

client = TestClient(app)

class TestSearchInputValidation(unittest.TestCase):
    """R1: Search Input Validation tests."""

    def test_hard_invalid_empty(self):
        r = client.post("/fetch", json={"query": ""})
        self.assertEqual(r.status_code, 422)
        data = r.json()
        self.assertFalse(data.get("valid"))
        self.assertIn("Please enter a valid hashtag", data.get("reason", ""))

    def test_hard_invalid_dots(self):
        r = client.post("/fetch", json={"query": "..."})
        self.assertEqual(r.status_code, 422)
        data = r.json()
        self.assertFalse(data.get("valid"))
        self.assertIn("Please enter a valid hashtag", data.get("reason", ""))

    def test_hard_invalid_repeated_char(self):
        r = client.post("/fetch", json={"query": "aaaaa"})
        self.assertEqual(r.status_code, 422)
        data = r.json()
        self.assertFalse(data.get("valid"))
        self.assertIn("Please enter a valid hashtag", data.get("reason", ""))

    def test_hard_invalid_unsupported_url(self):
        r = client.post("/fetch", json={"url": "https://twitter.com/someuser/status/123"})
        self.assertEqual(r.status_code, 422)
        data = r.json()
        self.assertFalse(data.get("valid"))
        self.assertIn("Only Bluesky and Mastodon links are supported", data.get("reason", ""))

    def test_accepted_short_acronyms(self):
        for acr in ["UK", "VPN", "ODI"]:
            val = sv.validate_and_plan(acr)
            self.assertTrue(val["valid"], f"Expected {acr} to be valid")
            self.assertFalse(val["is_soft_gibberish"], f"Expected {acr} not to be gibberish")

    def test_soft_gibberish_detection(self):
        val_xyz = sv.validate_and_plan("xyz..")
        self.assertTrue(val_xyz["valid"], "xyz.. should pass hard-invalid and proceed to search")
        self.assertTrue(val_xyz["is_soft_gibberish"], "xyz.. should be flagged as soft-gibberish")

        # When search returns 0 results for soft-gibberish, verify expected message
        with patch("live.search_bluesky", return_value=([], {"status": "ok"}, [])):
            with patch("live.search_mastodon", return_value=([], {"status": "ok"}, [])):
                res = live.search_live_keywords("xyz..")
                self.assertIn("No meaningful results for 'xyz..'. This looks like invalid input.", res["message"])

class TestQueryPlannerAndMockedHTTP(unittest.TestCase):
    """Mocked-HTTP unit tests for planner, thresholds and retries (R5, R6)."""

    def test_query_planner_variants_and_synonyms(self):
        plan = qp.plan_query("100 runs in odi")
        self.assertIn("100 runs", plan["keywords"])
        self.assertIn("odi", plan["keywords"])
        self.assertNotIn("in", plan["keywords"])
        # Synonyms
        self.assertTrue(any("century" in v for v in plan["variants"]))
        self.assertIn("#100runs", plan["variants"])
        self.assertIn("#odi", plan["variants"])

    def test_adaptive_thresholds_fallback(self):
        # Post with replyCount 0
        single_post = {
            "uri": "at://did:plc:test/app.bsky.feed.post/12345",
            "replyCount": 0,
            "author": {"handle": "alice.bsky.social"},
            "record": {"text": "Just watching cricket alone", "createdAt": "2026-10-08T00:00:00Z"}
        }

        with patch("live._bsky_search_call", return_value=(200, {"posts": [single_post]}, None)):
            with patch("live._fetch_bsky_single_thread") as mock_fetch:
                mock_fetch.return_value = {
                    "topic": "search:bsky:12345",
                    "platform": "Bluesky",
                    "rows": [{"id": "12345", "author": "alice.bsky.social", "body": "Just watching cricket", "platform": "Bluesky"}],
                    "first_50": "Just watching cricket",
                    "replies_count": 0,
                    "label": "Just watching cricket - Bluesky - no replies yet"
                }
                threads, diag, _ = live.search_bluesky({"variants": ["cricket"], "raw": "cricket"}, deadline=time_limit())
                self.assertEqual(len(threads), 1)
                self.assertIn("no replies yet", threads[0]["label"])

    def test_bluesky_session_refresh_on_401(self):
        with patch("requests.get") as mock_get:
            # First call 401, second call 200
            resp_401 = MagicMock()
            resp_401.status_code = 401
            resp_401.ok = False

            resp_200 = MagicMock()
            resp_200.status_code = 200
            resp_200.ok = True
            resp_200.json.return_value = {"posts": []}

            mock_get.side_effect = [resp_401, resp_200]
            with patch("live.get_bsky_token", return_value="refreshed_jwt_token"):
                status, data, err = live._bsky_search_call("test query", sort="top")
                self.assertEqual(status, 200)

def time_limit():
    import time
    return time.time() + 10.0

class TestGraphAndUserPanel(unittest.TestCase):
    """R3: Graph structure, parent->child arrows, user panel fields, location."""

    def test_graph_arrows_and_edge_attributes(self):
        r = client.get("/analysis/b7yijr?limit=300")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("links", data)
        self.assertGreater(len(data["links"]), 0)

        sample_edge = data["links"][0]
        self.assertIn("source", sample_edge)
        self.assertIn("target", sample_edge)
        self.assertIn("replies", sample_edge)
        self.assertIn("weight", sample_edge)

    def test_user_panel_fields_and_location(self):
        # In b7yijr, user voteronly10
        r = client.get("/user/b7yijr/voteronly10")
        self.assertEqual(r.status_code, 200)
        u = r.json()
        self.assertEqual(u["name"], "voteronly10")
        self.assertIn("first_share_utc", u)
        self.assertIn("last_5_posts", u)
        self.assertIn("location", u)
        # Reddit comments have no location field -> must be 'Not available'
        self.assertEqual(u["location"]["text"], "Not available")
        self.assertEqual(u["location"]["status"], "Not available")
        # Check rule details present
        self.assertIn("rule_details", u)
        self.assertIn("bot_score", u)

class TestSearchReliabilityQueries(unittest.TestCase):
    """R6 Acceptance Test for the required queries list."""

    QUERIES = [
        "odi cricket",
        "100 runs in odi",
        "century odi",
        "#odi",
        "vpn",
        "nepal flood",
        "vaccine",
        "mueller report",
        "buttigieg"
    ]

    def test_search_queries_reliability(self):
        print("\n" + "=" * 70)
        print("RUNNING SEARCH RELIABILITY ACCEPTANCE TESTS ACROSS REQUIRED QUERIES")
        print("=" * 70)

        results = {}
        success_count = 0

        for q in self.QUERIES:
            t0 = live.time.time()
            res = live.search_live_keywords(q)
            dur = live.time.time() - t0

            counts = res.get("counts", {})
            total_threads = counts.get("total", 0)
            diag = res.get("diagnostics", {})
            srcs = diag.get("sources", {})

            r_cnt = counts.get("reddit", 0)
            b_cnt = counts.get("bluesky", 0)
            m_cnt = counts.get("mastodon", 0)

            # Check if at least one thread or single-post result was found
            has_result = total_threads > 0 or (res.get("threads") and len(res["threads"]) > 0)
            if has_result:
                success_count += 1

            status_str = "PASS (found)" if has_result else "EXPLAINED (zero matches)"
            print(f"\nQuery: '{q}' [{dur:.2f}s] -> {status_str}")
            print(f"  Per-source thread counts: Reddit={r_cnt}, Bluesky={b_cnt}, Mastodon={m_cnt} (Total={total_threads})")
            print(f"  Summary message: {res.get('message')}")
            print(f"  Source Diagnostics: Reddit={srcs.get('reddit', {}).get('status')}, Bluesky={srcs.get('bluesky', {}).get('status')}, Mastodon={srcs.get('mastodon', {}).get('status')}")
            sys.stdout.flush()

            results[q] = {
                "total": total_threads,
                "counts": counts,
                "duration": dur,
                "has_result": has_result
            }

        success_rate = (success_count / len(self.QUERIES)) * 100.0
        print("\n" + "=" * 70)
        print(f"SEARCH RELIABILITY SUMMARY: {success_count}/{len(self.QUERIES)} queries returned results ({success_rate:.1f}%)")
        print("=" * 70)

        # Target: >= 90%
        self.assertGreaterEqual(
            success_rate, 90.0,
            f"Expected at least 90% success rate across required queries, got {success_rate:.1f}%"
        )

if __name__ == "__main__":
    unittest.main(verbosity=2)
