#!/usr/bin/env python3
"""
test_all_threads.py - Validates that all 5 threads load and analyze without errors.
Checks:
- All 5 threads in data/reddit.csv load properly
- Root post is labeled 'Earliest comment' since opening post is missing
- AutoModerator is NOT chosen as root in any thread
- Confidence label is either 'confident' or 'ambiguous'
- Communities in headline and badge only count communities with >= 5 accounts
"""
import os
import sys

# Ensure backend directory is in path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import store
import analytics as A
from collections import Counter

EXPECTED_THREADS = ["b7sa1t", "b80i87", "b7tup6", "b7sycp", "b7yijr"]

def test_load_all_threads():
    print("=" * 60)
    print("Testing Load and Analysis for all 5 Reddit Threads")
    print("=" * 60)

    df = store.load_reddit_df()
    assert df is not None and not df.empty, "reddit.csv could not be loaded"

    threads = list(df["root_id"].str.replace("t3_", "").unique())
    print(f"Discovered threads ({len(threads)}): {threads}")
    for exp in EXPECTED_THREADS:
        assert exp in threads, f"Expected thread {exp} not found in dataset"

    results = {}
    for t in EXPECTED_THREADS:
        print(f"\nAnalyzing thread '{t}'...")
        posts = store.load(t)
        assert posts, f"Thread {t} returned empty posts list"

        byid, pg, ug, rp, root_post = A.build(posts, t)
        assert ug.number_of_nodes() > 0, f"Graph has no nodes for thread {t}"

        comm = A.assign_communities(posts, ug)
        usr = A.users(posts, ug, comm)
        root = A.find_root(posts, t, byid, root_post, usr)
        summary = A.summary(posts, usr, root)

        # Assertions
        assert root is not None, f"Root not detected for thread {t}"
        author = root["author"]
        assert author.lower() != "automoderator", f"Thread {t} incorrectly picked AutoModerator as root!"
        assert root.get("post_type") == "Earliest comment", f"Thread {t} post_type expected 'Earliest comment', got {root.get('post_type')}"
        assert root["label"] in ("confident", "ambiguous"), f"Thread {t} invalid confidence label: {root['label']}"

        # Verify communities count in badge/headline counts only communities with >= 5 accounts
        comm_counts = Counter(u["community"] for u in usr.values() if u.get("community"))
        expected_comm_count = sum(1 for c in comm_counts.values() if c >= 5)
        assert summary["communities"] == expected_comm_count, (
            f"Thread {t} community count mismatch: got {summary['communities']}, expected {expected_comm_count}"
        )

        results[t] = {
            "posts": len(posts),
            "accounts": len(usr),
            "root_author": author,
            "root_label": root["label"],
            "post_type": root["post_type"],
            "communities_gte_5": summary["communities"],
            "total_communities": len(comm_counts),
            "headline": summary["headline"]
        }

        print(f"  [OK] Posts: {len(posts):,} | Users: {len(usr):,} | Earliest Author: {author} ({root['label']})")
        print(f"       Headline: {summary['headline']}")

    print("\n" + "=" * 60)
    print("SUCCESS: All 5 threads loaded and validated without errors!")
    print("=" * 60)
    return results

if __name__ == "__main__":
    test_load_all_threads()
