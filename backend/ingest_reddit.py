"""Load Reddit threads (Kaggle/Pushshift) into Neo4j.
  python ingest_reddit.py --csv ../data/thread_sample.csv
  python ingest_reddit.py --kaggle --roots t3_b7sa1t,t3_b7tup6 [--max-lines 5000000]
CSV columns: id, author, parent_id, root_id, body, created_utc, subreddit (subreddit drives community colours)."""
import argparse, io, json, glob, os
import pandas as pd

norm = lambda s: s.split("_", 1)[1] if isinstance(s, str) and s[:3] in ("t1_", "t3_") else (s or None)

def convert(rec):
    pid, me = norm(rec.get("parent_id")), norm(str(rec["id"]))
    return dict(id=me, author=rec.get("author") or "[deleted]", parent=None if pid == me else pid,
                body=str(rec.get("body") or "")[:500], ts=float(rec["created_utc"]) if rec.get("created_utc") else None,
                community=rec.get("subreddit") or "unknown", platform="reddit", topic=rec["root_id"])

def from_csv(path):
    df = pd.read_csv(path).fillna("")
    t = pd.to_numeric(df.created_utc, errors="coerce")
    if t.isna().all(): t = pd.to_datetime(df.created_utc, utc=True).astype("int64") // 10**9
    df["created_utc"] = t
    return [convert(r) for r in df.to_dict("records")]

def from_zst(path, roots, max_lines=0):
    import zstandard as zstd
    with open(path, "rb") as f, zstd.ZstdDecompressor(max_window_size=2**31).stream_reader(f) as r:
        for i, line in enumerate(io.TextIOWrapper(r, encoding="utf-8")):
            if max_lines and i >= max_lines: break
            try: o = json.loads(line)
            except ValueError: continue
            root = o.get("link_id") or "t3_" + o["id"]              # submissions have no link_id
            if root in roots:
                yield convert(dict(o, root_id=root, body=o.get("body") or f"{o.get('title','')} {o.get('selftext','')}"))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv"); ap.add_argument("--kaggle", action="store_true")
    ap.add_argument("--roots", default=""); ap.add_argument("--max-lines", type=int, default=0)
    a = ap.parse_args(); rows = []
    if a.csv: rows = from_csv(a.csv)
    elif a.kaggle:
        import kagglehub
        roots = set(filter(None, a.roots.split(",")))
        if not roots: ap.error("--kaggle needs --roots t3_xxx,t3_yyy")
        d = kagglehub.dataset_download("i221113hadiyatanveer/the-pushshift-reddit-dataset-submissions")
        for p in glob.glob(os.path.join(d, "**", "*.zst"), recursive=True):
            rows += list(from_zst(p, roots, a.max_lines))
    else: ap.error("give --csv or --kaggle")
    import store
    print(f"saved {store.save(rows)} posts across {len({r['topic'] for r in rows})} threads")
