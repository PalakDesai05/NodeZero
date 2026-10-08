#!/usr/bin/env python3
"""
Test /analysis through the real HTTP stack (Starlette/FastAPI TestClient)
Verifies:
- All 5 Reddit threads (b7sa1t, b80i87, b7tup6, b7sycp, b7yijr)
- One Bluesky search thread
- One Mastodon search thread
- Asserts HTTP status 200, non-empty nodes list, and Access-Control-Allow-Origin header
- Asserts global exception handler on HTTP 500 preserves CORS headers and JSON format
"""
import os
import sys
import unittest
from fastapi.testclient import TestClient

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import store
from main import app

class TestAnalysisHttpStack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.origin_header = {"Origin": "http://localhost:5173"}

        # Ensure we have a Bluesky search thread in store
        cls.bsky_topic = "search:bsky:test_thread_1"
        bsky_rows = [
            {
                "id": "bsky:root1",
                "author": "alice.bsky.social",
                "parent_id": None,
                "parent": None,
                "root_id": "bsky:root1",
                "body": "Root post on Bluesky about network propagation",
                "created_utc": 1739000000.0,
                "ts": 1739000000.0,
                "subreddit": "bsky.social",
                "community": "bsky.social",
                "platform": "Bluesky",
                "topic": cls.bsky_topic
            },
            {
                "id": "bsky:reply1",
                "author": "bob.bsky.social",
                "parent_id": "bsky:root1",
                "parent": "bsky:root1",
                "root_id": "bsky:root1",
                "body": "First reply agreeing with root",
                "created_utc": 1739000100.0,
                "ts": 1739000100.0,
                "subreddit": "bsky.social",
                "community": "bsky.social",
                "platform": "Bluesky",
                "topic": cls.bsky_topic
            },
            {
                "id": "bsky:reply2",
                "author": "charlie.bsky.social",
                "parent_id": "bsky:reply1",
                "parent": "bsky:reply1",
                "root_id": "bsky:root1",
                "body": "Nested reply on Bluesky",
                "created_utc": 1739000200.0,
                "ts": 1739000200.0,
                "subreddit": "bsky.social",
                "community": "bsky.social",
                "platform": "Bluesky",
                "topic": cls.bsky_topic
            }
        ]
        store.save_search_thread(cls.bsky_topic, bsky_rows, {
            "platform": "Bluesky",
            "label": "Bluesky test search thread - 2 replies",
            "first_50": "Root post on Bluesky about network propagation",
            "replies": 2,
            "query": "vpn",
            "relevance": 15
        })

        # Ensure we have a Mastodon search thread in store
        cls.masto_topic = "search:masto:mastodon.social:test_status_1"
        masto_rows = [
            {
                "id": "mastodon.social:1001",
                "author": "mastouser1@mastodon.social",
                "parent_id": None,
                "parent": None,
                "root_id": "mastodon.social:1001",
                "body": "Root Mastodon status exploring decentralized cascade networks",
                "created_utc": 1739100000.0,
                "ts": 1739100000.0,
                "subreddit": "mastodon.social",
                "community": "mastodon.social",
                "platform": "Mastodon",
                "topic": cls.masto_topic
            },
            {
                "id": "mastodon.social:1002",
                "author": "mastouser2@mastodon.social",
                "parent_id": "mastodon.social:1001",
                "parent": "mastodon.social:1001",
                "root_id": "mastodon.social:1001",
                "body": "Replying to Mastodon root post",
                "created_utc": 1739100150.0,
                "ts": 1739100150.0,
                "subreddit": "mastodon.social",
                "community": "mastodon.social",
                "platform": "Mastodon",
                "topic": cls.masto_topic
            }
        ]
        store.save_search_thread(cls.masto_topic, masto_rows, {
            "platform": "Mastodon",
            "label": "Mastodon test search thread - 1 replies",
            "first_50": "Root Mastodon status exploring decentralized cascade",
            "replies": 1,
            "query": "odi cricket",
            "relevance": 12
        })

    def _verify_analysis_response(self, topic: str):
        url = f"/analysis/{topic}?limit=300"
        res = self.client.get(url, headers=self.origin_header)

        # 1. Assert status 200
        self.assertEqual(res.status_code, 200, f"Expected 200 for topic {topic}, got {res.status_code}: {res.text}")

        # 2. Assert CORS header present
        cors_hdr = res.headers.get("access-control-allow-origin")
        self.assertIsNotNone(cors_hdr, f"Missing Access-Control-Allow-Origin header for {topic}")
        self.assertEqual(cors_hdr, "*", f"Expected Access-Control-Allow-Origin: * for {topic}")

        # 3. Assert non-empty nodes list
        data = res.json()
        nodes = data.get("nodes", [])
        links = data.get("links", [])
        self.assertIsInstance(nodes, list)
        self.assertGreater(len(nodes), 0, f"Expected non-empty nodes list for topic {topic}, got {len(nodes)}")

        print(f"  [PASS] /analysis/{topic:45} -> HTTP {res.status_code} | Nodes: {len(nodes):4d} | Links: {len(links):4d} | CORS: {cors_hdr}")
        return data

    def test_all_five_reddit_threads(self):
        print("\n--- Verifying All 5 Reddit Threads via HTTP Stack ---")
        reddit_threads = ["b7sa1t", "b80i87", "b7tup6", "b7sycp", "b7yijr"]
        for topic in reddit_threads:
            self._verify_analysis_response(topic)

    def test_bluesky_search_thread(self):
        print("\n--- Verifying Bluesky Search Thread via HTTP Stack ---")
        self._verify_analysis_response(self.bsky_topic)

    def test_mastodon_search_thread(self):
        print("\n--- Verifying Mastodon Search Thread via HTTP Stack ---")
        self._verify_analysis_response(self.masto_topic)

    def test_global_exception_handler_cors_on_500(self):
        print("\n--- Verifying Global Exception Handler on HTTP 500 ---")
        res = self.client.get("/test/error500", headers=self.origin_header)
        self.assertEqual(res.status_code, 500)
        cors_hdr = res.headers.get("access-control-allow-origin")
        self.assertEqual(cors_hdr, "*")
        data = res.json()
        self.assertIn("error", data)
        self.assertIn("detail", data)
        print(f"  [PASS] /test/error500 -> HTTP {res.status_code} | Error: {data['error']} | Detail: {data['detail']} | CORS: {cors_hdr}")

if __name__ == "__main__":
    unittest.main(verbosity=2)
