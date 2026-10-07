import os
import time
import pickle
from collections import defaultdict
import pandas as pd

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
REDDIT_CSV = os.path.join(DATA_DIR, "reddit.csv")
CACHE_DIR = os.path.join(DATA_DIR, ".cache")

os.makedirs(CACHE_DIR, exist_ok=True)

LIVE_POSTS = {}        # topic -> list of dicts
SEARCH_POSTS = {}      # topic -> list of dicts
SEARCH_METADATA = {}   # topic -> metadata dict (label, platform, query, replies)

STORE_PKL = os.path.join(CACHE_DIR, "live_store.pkl")

def _load_persisted_store():
    global LIVE_POSTS, SEARCH_POSTS, SEARCH_METADATA
    if os.path.exists(STORE_PKL):
        try:
            with open(STORE_PKL, "rb") as f:
                data = pickle.load(f)
                LIVE_POSTS = data.get("live", {})
                SEARCH_POSTS = data.get("search", {})
                SEARCH_METADATA = data.get("search_meta", {})
        except Exception:
            pass

def _save_persisted_store():
    try:
        with open(STORE_PKL, "wb") as f:
            pickle.dump({
                "live": LIVE_POSTS,
                "search": SEARCH_POSTS,
                "search_meta": SEARCH_METADATA
            }, f)
    except Exception:
        pass

_load_persisted_store()

# In-memory caches
MEMORY_ANALYSIS_CACHE = {}   # topic -> {"mtime": float, "data": dict}
ACCOUNT_INDEX_CACHE = {"mtime": None, "index": None, "sources": []}

def has_reddit_csv():
    return os.path.exists(REDDIT_CSV)

def get_csv_mtime():
    if not has_reddit_csv():
        return 0.0
    try:
        return os.path.getmtime(REDDIT_CSV)
    except Exception:
        return 0.0

def load_reddit_df():
    if not has_reddit_csv():
        return None
    try:
        return pd.read_csv(REDDIT_CSV, dtype=str).fillna("")
    except Exception:
        return None

def save_live(topic, rows):
    clean_topic = topic.replace("t3_", "")
    LIVE_POSTS[clean_topic] = rows
    _save_persisted_store()
    # Invalidate memory & disk cache for this topic
    invalidate_cache(clean_topic)
    invalidate_account_index()

def save_search_thread(topic, rows, metadata):
    clean_topic = topic.replace("t3_", "")
    SEARCH_POSTS[clean_topic] = rows
    SEARCH_METADATA[clean_topic] = metadata
    _save_persisted_store()
    invalidate_cache(clean_topic)
    invalidate_account_index()

def invalidate_cache(topic):
    clean_topic = topic.replace("t3_", "")
    if clean_topic in MEMORY_ANALYSIS_CACHE:
        del MEMORY_ANALYSIS_CACHE[clean_topic]
    disk_path = os.path.join(CACHE_DIR, f"{clean_topic}.pkl")
    if os.path.exists(disk_path):
        try:
            os.remove(disk_path)
        except Exception:
            pass

def invalidate_account_index():
    ACCOUNT_INDEX_CACHE["mtime"] = None
    ACCOUNT_INDEX_CACHE["index"] = None

def get_cached_analysis(topic, limit=300):
    clean_topic = topic.replace("t3_", "")
    csv_mtime = get_csv_mtime()
    
    # Check in-memory cache first
    mem = MEMORY_ANALYSIS_CACHE.get(clean_topic)
    if mem and mem.get("mtime") == csv_mtime and mem.get("limit") == limit:
        return mem.get("data")
    
    # Check disk cache (pickle)
    disk_path = os.path.join(CACHE_DIR, f"{clean_topic}_{limit}.pkl")
    if os.path.exists(disk_path):
        try:
            with open(disk_path, "rb") as f:
                saved = pickle.load(f)
                if saved.get("mtime") == csv_mtime:
                    # Warm memory cache
                    MEMORY_ANALYSIS_CACHE[clean_topic] = {
                        "mtime": csv_mtime,
                        "limit": limit,
                        "data": saved.get("data")
                    }
                    return saved.get("data")
        except Exception:
            pass
    return None

def set_cached_analysis(topic, limit, data):
    clean_topic = topic.replace("t3_", "")
    csv_mtime = get_csv_mtime()
    MEMORY_ANALYSIS_CACHE[clean_topic] = {
        "mtime": csv_mtime,
        "limit": limit,
        "data": data
    }
    disk_path = os.path.join(CACHE_DIR, f"{clean_topic}_{limit}.pkl")
    try:
        with open(disk_path, "wb") as f:
            pickle.dump({"mtime": csv_mtime, "limit": limit, "data": data}, f)
    except Exception:
        pass

def topics():
    """
    Returns thread listings grouped by source.
    Reddit threads come exclusively from data/reddit.csv.
    Bluesky/Mastodon links appear under 'live'.
    Search results appear under 'search_results'.
    """
    reddit_threads = []
    error = None
    if not has_reddit_csv():
        error = "Run fetch_reddit.py first"
    else:
        df = load_reddit_df()
        if df is not None and not df.empty and "root_id" in df.columns:
            df["clean_root"] = df["root_id"].str.replace("t3_", "")
            counts = df.groupby("clean_root").size()
            for root_id, count in counts.items():
                reddit_threads.append({
                    "topic": str(root_id),
                    "platform": "reddit",
                    "comments": int(count),
                    "posts": int(count)
                })
            reddit_threads.sort(key=lambda x: -x["comments"])

    live_threads = []
    for t, posts in LIVE_POSTS.items():
        plat = posts[0].get("platform", "live") if posts else "live"
        live_threads.append({
            "topic": t,
            "platform": plat,
            "comments": len(posts),
            "posts": len(posts)
        })

    search_threads = []
    for t, posts in SEARCH_POSTS.items():
        meta = SEARCH_METADATA.get(t, {})
        plat = meta.get("platform") or (posts[0].get("platform", "live") if posts else "live")
        label = meta.get("label") or f"{t[:50]} - {plat} - {len(posts)} replies"
        search_threads.append({
            "topic": t,
            "platform": plat,
            "comments": len(posts),
            "posts": len(posts),
            "label": label
        })

    return {
        "reddit": reddit_threads,
        "live": live_threads,
        "search_results": search_threads,
        "error": error
    }

def load(topic):
    clean_topic = topic.replace("t3_", "")

    # Check live posts first
    if clean_topic in LIVE_POSTS:
        return LIVE_POSTS[clean_topic]
    if topic in LIVE_POSTS:
        return LIVE_POSTS[topic]

    # Check search posts
    if clean_topic in SEARCH_POSTS:
        return SEARCH_POSTS[clean_topic]
    if topic in SEARCH_POSTS:
        return SEARCH_POSTS[topic]

    if not has_reddit_csv():
        return None

    df = load_reddit_df()
    if df is None or df.empty:
        return None

    df["clean_root"] = df["root_id"].str.replace("t3_", "")
    sub = df[df["clean_root"] == clean_topic]
    if sub.empty:
        return None

    rows = []
    for r in sub.to_dict("records"):
        cid = str(r.get("id", "")).replace("t1_", "").replace("t3_", "")
        pid = str(r.get("parent_id", ""))
        ts_val = 0
        try:
            ts_val = float(r.get("created_utc", 0))
        except (ValueError, TypeError):
            pass
        rows.append({
            "id": cid,
            "author": r.get("author") or "[deleted]",
            "parent_id": pid,
            "parent": pid.replace("t1_", "").replace("t3_", "") if pid else None,
            "root_id": clean_topic,
            "topic": clean_topic,
            "body": r.get("body", ""),
            "created_utc": ts_val,
            "ts": ts_val,
            "subreddit": r.get("subreddit") or "unknown",
            "community": r.get("subreddit") or "unknown",
            "platform": "reddit"
        })
    return rows

def _build_account_index():
    csv_mtime = get_csv_mtime()
    if ACCOUNT_INDEX_CACHE["mtime"] == csv_mtime and ACCOUNT_INDEX_CACHE["index"] is not None:
        return ACCOUNT_INDEX_CACHE["index"], ACCOUNT_INDEX_CACHE["sources"]

    account_map = defaultdict(lambda: defaultdict(int))
    sources_summary = []

    # 1. Reddit accounts
    df = load_reddit_df()
    if df is not None and not df.empty and "author" in df.columns and "root_id" in df.columns:
        df["clean_root"] = df["root_id"].str.replace("t3_", "")
        reddit_threads = list(df["clean_root"].unique())
        sources_summary.append(f"Reddit ({len(reddit_threads)} threads: {', '.join(reddit_threads)})")
        grouped = df.groupby(["author", "clean_root"]).size()
        for (author, th), count in grouped.items():
            if author and author not in ("[deleted]", "[removed]"):
                account_map[str(author)][str(th)] += int(count)

    # 2. Live threads
    if LIVE_POSTS:
        live_names = list(LIVE_POSTS.keys())
        sources_summary.append(f"Live ({len(live_names)} threads: {', '.join(live_names)})")
        for th, posts in LIVE_POSTS.items():
            for p in posts:
                auth = p.get("author")
                if auth and auth not in ("[deleted]", "[removed]"):
                    account_map[str(auth)][str(th)] += 1

    # 3. Search result threads
    if SEARCH_POSTS:
        search_names = list(SEARCH_POSTS.keys())
        sources_summary.append(f"Search Results ({len(search_names)} threads)")
        for th, posts in SEARCH_POSTS.items():
            for p in posts:
                auth = p.get("author")
                if auth and auth not in ("[deleted]", "[removed]"):
                    account_map[str(auth)][str(th)] += 1

    ACCOUNT_INDEX_CACHE["mtime"] = csv_mtime
    ACCOUNT_INDEX_CACHE["index"] = account_map
    ACCOUNT_INDEX_CACHE["sources"] = sources_summary
    return account_map, sources_summary

def search_accounts(query: str, limit: int = 10):
    """
    Search ALL accounts in the loaded data, not just the open thread's top 300.
    Case-insensitive, partial match, returns up to 10 matches.
    If not found, reports which source and threads were searched.
    """
    q = (query or "").strip().lower()
    account_map, sources = _build_account_index()

    if not q:
        return {
            "query": query,
            "matches": [],
            "searched_sources": sources,
            "message": "Empty query"
        }

    matches = []
    for author, thread_counts in account_map.items():
        if q in author.lower():
            # Find thread where this account has the most comments
            best_thread = max(thread_counts, key=thread_counts.get)
            comments_in_best = thread_counts[best_thread]
            total_comments = sum(thread_counts.values())

            # Score for ranking: exact match > prefix match > substring match
            rank_score = 3 if author.lower() == q else 2 if author.lower().startswith(q) else 1

            matches.append({
                "name": author,
                "best_thread": best_thread,
                "comments_in_best": comments_in_best,
                "total_comments": total_comments,
                "threads": dict(thread_counts),
                "_rank": (rank_score, total_comments)
            })

    # Sort: highest rank score first, then most comments
    matches.sort(key=lambda x: (x["_rank"][0], x["_rank"][1]), reverse=True)
    top_matches = matches[:limit]
    for m in top_matches:
        del m["_rank"]

    found = len(top_matches) > 0
    src_str = "; ".join(sources) if sources else "no data loaded"
    msg = f"Found {len(top_matches)} matches across loaded data" if found else f"No account matching '{query}' found. Searched sources: {src_str}"

    return {
        "query": query,
        "matches": top_matches,
        "total_matches": len(matches),
        "searched_sources": sources,
        "message": msg
    }
