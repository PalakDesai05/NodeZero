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
    
    safe_file_topic = clean_topic.replace(":", "_")
    # Check disk cache (pickle)
    disk_path = os.path.join(CACHE_DIR, f"{safe_file_topic}_{limit}.pkl")
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
    safe_file_topic = clean_topic.replace(":", "_")
    csv_mtime = get_csv_mtime()
    MEMORY_ANALYSIS_CACHE[clean_topic] = {
        "mtime": csv_mtime,
        "limit": limit,
        "data": data
    }
    disk_path = os.path.join(CACHE_DIR, f"{safe_file_topic}_{limit}.pkl")
    try:
        with open(disk_path, "wb") as f:
            pickle.dump({"mtime": csv_mtime, "limit": limit, "data": data}, f)
    except Exception:
        pass

def clear_search_results():
    global SEARCH_POSTS, SEARCH_METADATA
    SEARCH_POSTS.clear()
    SEARCH_METADATA.clear()
    _save_persisted_store()
    invalidate_account_index()

def search_reddit(query: str, query_plan: dict = None) -> list[dict]:
    """
    Search Reddit CSV full text (case-insensitive, whole words only with regex \b).
    Rank: exact phrase > all keywords > at least 2 keywords.
    A single-keyword match is only a "weak match" (for len <= 2 keywords).
    Never count more than 3 weak matches.
    Multi-keyword queries with >= 3 keywords require at least 2 keywords to match.
    """
    if not has_reddit_csv():
        return []

    df = load_reddit_df()
    if df is None or df.empty or "body" not in df.columns:
        return []

    df["clean_root"] = df["root_id"].str.replace("t3_", "")
    clean_q = (query or "").strip().lower().replace("#", "")
    if not clean_q:
        return []

    import re
    try:
        from search_validator import STOPWORDS
    except ImportError:
        STOPWORDS = {
            "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are",
            "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but",
            "by", "did", "do", "does", "doing", "down", "during", "each", "few", "for", "from",
            "had", "has", "have", "having", "he", "her", "here", "him", "his", "how", "i", "if",
            "in", "into", "is", "it", "its", "me", "more", "most", "my", "no", "nor", "not", "of",
            "off", "on", "once", "only", "or", "other", "our", "out", "over", "own", "same", "she",
            "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "then", "there",
            "these", "they", "this", "those", "through", "to", "too", "under", "until", "up", "very",
            "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why",
            "will", "with", "you", "your", "yours", "ke", "ka", "ki", "hai", "ko", "se", "ne"
        }

    if query_plan and query_plan.get("keywords"):
        raw_kws = query_plan["keywords"]
    else:
        raw_kws = re.findall(r"\b\w+\b", clean_q)

    # Drop English & Hindi/Hinglish stopwords and single-letter characters
    keywords = [
        k.lower().strip() for k in raw_kws
        if k.lower().strip() not in STOPWORDS and len(k.strip()) >= 2
    ]
    # Deduplicate while preserving order
    dedup_kws = []
    for k in keywords:
        if k not in dedup_kws:
            dedup_kws.append(k)
    keywords = dedup_kws
    total_keywords = len(keywords)

    # Whole-word regex pattern for exact phrase
    phrase_pattern = r"\b" + re.escape(clean_q) + r"\b"

    results = []
    for root_id, group in df.groupby("clean_root"):
        # 1. Exact phrase match with \b
        phrase_mask = group["body"].str.contains(phrase_pattern, case=False, na=False, regex=True)
        has_exact_phrase = bool(phrase_mask.any())
        phrase_matches = int(phrase_mask.sum())

        # 2. Whole word matches for each keyword
        matched_kws = []
        matching_rows = []
        if has_exact_phrase:
            matching_rows = group[phrase_mask]

        total_kw_matches = 0
        for kw in keywords:
            kw_pattern = r"\b" + re.escape(kw) + r"\b"
            kw_mask = group["body"].str.contains(kw_pattern, case=False, na=False, regex=True)
            kw_count = int(kw_mask.sum())
            if kw_count > 0:
                matched_kws.append(kw)
                total_kw_matches += kw_count
                if len(matching_rows) == 0:
                    matching_rows = group[kw_mask]

        num_matched = len(matched_kws)

        # Classify match quality:
        # Tier 1: Exact phrase
        # Tier 2: All keywords
        # Tier 3: At least 2 keywords (Partial: X of Y)
        # Tier 4: Weak match (1 keyword)
        if has_exact_phrase:
            quality_tier = 1
            quality_label = "[Exact phrase]"
            is_match = True
        elif total_keywords > 0 and num_matched == total_keywords:
            quality_tier = 2
            quality_label = "[All keywords]"
            is_match = True
        elif num_matched >= 2:
            quality_tier = 3
            quality_label = f"[Partial: {num_matched} of {total_keywords}]"
            is_match = True
        elif num_matched == 1:
            # Multi-keyword queries with >= 3 keywords require at least 2 keywords to match
            # "khatron ke khiladi winner" has 3 keywords and matches only 1 -> returns 0
            if total_keywords >= 3:
                is_match = False
            else:
                quality_tier = 4
                quality_label = "[Weak: 1 keyword]"
                is_match = True
        else:
            is_match = False

        if not is_match:
            continue

        best_snippet = "Reddit discussion thread"
        if len(matching_rows) > 0:
            first_match_body = matching_rows.iloc[0]["body"] or ""
            clean_snippet = " ".join(first_match_body.strip().split())
            if clean_snippet:
                best_snippet = clean_snippet[:50]

        thread_posts = load(str(root_id)) or []
        replies_count = max(0, len(thread_posts) - 1)
        label = f"{quality_label} {best_snippet} - Reddit (2019) - {replies_count} replies"

        relevance = (100 if quality_tier == 1 else 70 if quality_tier == 2 else 40 if quality_tier == 3 else 10) + (phrase_matches * 5) + total_kw_matches

        results.append({
            "topic": str(root_id),
            "platform": "Reddit (2019)",
            "rows": thread_posts,
            "first_50": best_snippet,
            "replies_count": replies_count,
            "quality_tier": quality_tier,
            "quality_label": quality_label,
            "relevance": relevance,
            "label": label,
            "posts_count": len(thread_posts)
        })

    # Separate strong matches and weak matches
    strong_results = [r for r in results if r["quality_tier"] < 4]
    weak_results = [r for r in results if r["quality_tier"] == 4]

    # Never count more than 3 weak matches
    weak_results.sort(key=lambda x: (x["quality_tier"], -x["replies_count"]))
    weak_results = weak_results[:3]

    combined = strong_results + weak_results
    # Sort by match quality first (quality_tier 1 > 2 > 3 > 4), then by replies (descending)
    combined.sort(key=lambda x: (x["quality_tier"], -x["replies_count"]))
    return combined

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
        q_label = meta.get("quality_label", "")
        label = meta.get("label") or f"{q_label} {t[:50]} - {plat} - {len(posts)} replies".strip()
        relevance = meta.get("relevance", 0)
        replies = meta.get("replies", max(0, len(posts) - 1))
        q_tier = meta.get("quality_tier", 4)
        search_threads.append({
            "topic": t,
            "platform": plat,
            "comments": len(posts),
            "posts": len(posts),
            "label": label,
            "quality_tier": q_tier,
            "quality_label": q_label,
            "relevance": relevance,
            "replies": replies
        })

    # Sort by match quality first (quality_tier 1 < 2 < 3 < 4), then by reply count (descending)
    search_threads.sort(key=lambda x: (x.get("quality_tier", 4), -x.get("replies", 0)))

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
