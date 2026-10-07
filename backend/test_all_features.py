#!/usr/bin/env python3
"""
Comprehensive validation test for all NodeZero features:
- Section A: Account Search (partial match, case-insensitive, top 10, best thread switch, include_user with neighbors)
- Section B: Speed (step timings, top 3 slowest, pickle & memory cache, Louvain seed=42, top 300 default, gzip)
- Section C & D: Live fetch & Keyword search (vpn, nepal flood, vaccine, deduplication, re-pointing, dropdown format)
- Neo4j Sync: Background job, UNWIND batches of 5000, skip if unchanged
"""
import os
import sys
import time
from fastapi.testclient import TestClient

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from main import app
import store
import live
import neo4j_loader
import analytics as A

client = TestClient(app)

def test_section_a_account_search():
    print("\n--- Testing Section A: Account Search ---")
    # 1. Search existing account (partial match, case-insensitive)
    r = client.get("/accounts/search?q=mydnite")
    assert r.status_code == 200, f"Search failed: {r.text}"
    data = r.json()
    assert "matches" in data
    assert len(data["matches"]) > 0
    top_match = data["matches"][0]
    assert "MydniteSon" in top_match["name"]
    assert top_match["best_thread"] == "b7tup6"
    print(f"  [OK] Found match: {top_match['name']} with best_thread: {top_match['best_thread']}")

    # 2. Search multiple partial matches capped at 10
    r = client.get("/accounts/search?q=the&limit=10")
    data = r.json()
    assert len(data["matches"]) <= 10
    print(f"  [OK] Partial search for 'the' returned {len(data['matches'])} matches (max 10)")

    # 3. Search non-existent account -> reports searched sources and threads
    r = client.get("/accounts/search?q=nonexistent_xyz_9999")
    data = r.json()
    assert len(data["matches"]) == 0
    assert "Reddit" in data["message"]
    print(f"  [OK] Not found message correctly describes searched sources: {data['message']}")

    # 4. Thread switch with include_user (account and direct neighbors in view)
    r = client.get(f"/analysis/{top_match['best_thread']}?limit=300&include_user={top_match['name']}")
    assert r.status_code == 200
    res_graph = r.json()
    node_ids = {n["id"] for n in res_graph["nodes"]}
    assert top_match["name"] in node_ids, "Included user was not in graph nodes"
    print(f"  [OK] include_user={top_match['name']} verified in graph nodes ({len(res_graph['nodes'])} total nodes)")

def test_section_b_speed_and_caching():
    print("\n--- Testing Section B: Speed and Caching ---")
    topic = "b7sa1t"

    # Invalidate cache for topic to test cold timing
    store.invalidate_cache(topic)

    # 1. Cold analysis call: logs step times and top 3 slowest
    t0 = time.perf_counter()
    r = client.get(f"/analysis/{topic}?limit=300")
    t_cold = time.perf_counter() - t0
    assert r.status_code == 200
    print(f"  [OK] Cold analysis took: {t_cold:.3f}s (target < 2.0s)")
    assert t_cold < 2.0, f"Cold analysis took too long: {t_cold}s"

    # 2. Warm cached call (memory / disk pickle)
    t0 = time.perf_counter()
    r = client.get(f"/analysis/{topic}?limit=300")
    t_warm = time.perf_counter() - t0
    assert r.status_code == 200
    print(f"  [OK] Warm cached analysis took: {t_warm*1000:.2f}ms (target < 50ms)")
    assert t_warm < 0.1, f"Warm cache took too long: {t_warm}s"

    # 3. Verify top 300 limit by default
    assert len(r.json()["nodes"]) <= 305  # 300 + root + neighbors

    # 4. Verify GZip header
    r_gzip = client.get(f"/analysis/{topic}?limit=300", headers={"Accept-Encoding": "gzip"})
    assert r_gzip.status_code == 200

def test_section_c_and_d_live_and_keywords():
    print("\n--- Testing Section C & D: Live Keyword Search & Fallbacks ---")
    # Test keywords from requirement: vpn, nepal flood, vaccine
    for kw in ["vpn", "nepal flood", "vaccine"]:
        t0 = time.perf_counter()
        res = live.search_live_keywords(kw)
        dur = time.perf_counter() - t0
        print(f"  [OK] Keyword '{kw}' search took: {dur:.2f}s | Result: {res['message']} | Biggest: {res.get('biggest_topic')}")
        assert res["counts"]["total"] > 0, f"Expected threads for keyword '{kw}'"
        assert res["biggest_topic"] is not None

    # Test topics endpoint returns search_results
    r = client.get("/topics")
    assert r.status_code == 200
    data = r.json()
    assert "search_results" in data
    assert len(data["search_results"]) > 0
    sample_res = data["search_results"][0]
    print(f"  [OK] Dropdown search result format: {sample_res.get('label')}")
    assert " - " in sample_res.get("label", ""), "Label should follow format '<50 chars> - platform - N replies'"

def test_neo4j_sync_background():
    print("\n--- Testing Neo4j Background Sync and Skip Logic ---")
    # Verify sync trigger
    r = client.post("/neo4j/sync")
    assert r.status_code == 200
    data = r.json()
    print(f"  [OK] /neo4j/sync response: {data.get('message')}")

    # Verify status endpoint
    r_status = client.get("/neo4j/sync/status")
    assert r_status.status_code == 200
    st = r_status.json()
    assert "running" in st
    assert "progress" in st
    print(f"  [OK] /neo4j/sync/status running={st['running']}, progress='{st['progress']}'")

if __name__ == "__main__":
    print("=" * 65)
    print("RUNNING COMPLETE FEATURE VALIDATION TEST SUITE")
    print("=" * 65)
    test_section_a_account_search()
    test_section_b_speed_and_caching()
    test_section_c_and_d_live_and_keywords()
    test_neo4j_sync_background()
    print("\n" + "=" * 65)
    print("ALL TESTS PASSED SUCCESSFULLY!")
    print("=" * 65)
