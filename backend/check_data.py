#!/usr/bin/env python3
"""
check_data.py - Data verification and consistency checks for NodeZero.
Prints:
1. Row count
2. Count of IDs containing 'demo' (must be 0)
3. Percent of replies whose parent is in the file
4. Comments per thread
"""
import os
import sys
import pandas as pd

def find_csv_path():
    candidates = [
        os.path.join(os.path.dirname(__file__), "data", "reddit.csv"),
        os.path.join(os.path.dirname(__file__), "..", "data", "reddit.csv"),
        os.path.join(os.getcwd(), "data", "reddit.csv"),
        os.path.join(os.getcwd(), "..", "data", "reddit.csv"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    raise FileNotFoundError("Could not locate data/reddit.csv")

def clean_id(s):
    if isinstance(s, str) and (s.startswith("t1_") or s.startswith("t3_")):
        return s.split("_", 1)[1]
    return str(s) if s is not None else ""

def check_data():
    csv_path = find_csv_path()
    df = pd.read_csv(csv_path, dtype=str).fillna("")

    row_count = len(df)
    demo_count = int(df["id"].str.contains("demo", case=False, na=False).sum())

    all_ids = set(df["id"].apply(clean_id))
    clean_parents = df["parent_id"].apply(clean_id)
    in_file_count = int(clean_parents.isin(all_ids).sum())
    pct_replies = (in_file_count / row_count * 100.0) if row_count > 0 else 0.0

    thread_counts = df["root_id"].str.replace("t3_", "").value_counts()

    print("=" * 60)
    print("NodeZero Dataset Verification Report")
    print("=" * 60)
    print(f"Dataset path: {csv_path}")
    print(f"Row count: {row_count}")
    print(f"Count of IDs containing 'demo': {demo_count}")
    print(f"Percent of replies whose parent is in the file: {pct_replies:.2f}% ({in_file_count}/{row_count})")
    print("\nComments per thread:")
    for thread, count in thread_counts.items():
        print(f"  - {thread}: {count:,} comments")
    print("=" * 60)

    if demo_count != 0:
        print(f"ERROR: Found {demo_count} rows with 'demo' in ID (expected 0)!", file=sys.stderr)
        return False
    return True

if __name__ == "__main__":
    success = check_data()
    if not success:
        sys.exit(1)
