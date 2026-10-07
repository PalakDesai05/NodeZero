import time
import networkx as nx
import numpy as np
from collections import Counter, defaultdict
from networkx.algorithms.community import louvain_communities
import detector

# Expose louvain_communities under nx.community for any external callers
if not hasattr(nx, "community"):
    import types
    nx.community = types.ModuleType("nx.community")
nx.community.louvain_communities = louvain_communities

FLAG = detector.FLAG_THRESHOLD  # 60

def clean_id(s):
    if isinstance(s, str) and (s.startswith("t1_") or s.startswith("t3_")):
        return s.split("_", 1)[1]
    return str(s) if s is not None else ""

def get_author_node(p):
    """
    Each [deleted] comment is its own node.
    """
    auth = p.get("author") or "[deleted]"
    if auth in ("[deleted]", "[removed]"):
        return f"[deleted:{clean_id(p['id'])}]"
    return auth

def build(posts, topic=""):
    """
    Graph construction:
    - Node = account
    - Edge = replier -> replied-to account via parent_id (strip t1_/t3_)
    - If the parent is not in the file, attach to the thread's root placeholder
    - Each [deleted] comment is its own node
    - Edge weight = number of replies
    """
    clean_topic = clean_id(topic)
    byid = {clean_id(p["id"]): p for p in posts}

    # Post graph: child -> parent (for tracing)
    pg = nx.DiGraph()
    for pid, p in byid.items():
        pg.add_node(pid)
        raw_p = p.get("parent_id") or p.get("parent")
        if raw_p:
            pg.add_edge(pid, clean_id(raw_p))

    # Check if root submission exists in the file
    root_post = byid.get(clean_topic)
    if not root_post:
        # For live posts or posts where topic doesn't match clean_id directly
        for p in posts:
            if clean_id(p.get("id")) == clean_id(p.get("root_id")) or (not p.get("parent_id") and not p.get("parent")):
                root_post = p
                break

    root_placeholder = get_author_node(root_post) if root_post else f"[root:{clean_topic}]"

    # User graph: replier -> replied-to
    ug = nx.DiGraph()
    for p in posts:
        ug.add_node(get_author_node(p))
    if not root_post:
        ug.add_node(root_placeholder)

    for p in posts:
        replier = get_author_node(p)
        raw_p = p.get("parent_id") or p.get("parent")
        if not raw_p:
            replied_to = root_placeholder
        else:
            clean_parent = clean_id(raw_p)
            if clean_parent == clean_topic:
                replied_to = root_placeholder
            elif clean_parent in byid:
                replied_to = get_author_node(byid[clean_parent])
            else:
                # Parent is not in the file -> attach to thread's root placeholder
                replied_to = root_placeholder

        if replier != replied_to:
            current_w = ug.get_edge_data(replier, replied_to, {"weight": 0})["weight"]
            ug.add_edge(replier, replied_to, weight=current_w + 1)

    return byid, pg, ug, root_placeholder, root_post

def assign_communities(posts, ug):
    """
    Node colour: by subreddit when several subreddits are loaded;
    for a single thread (one subreddit) use detected communities
    (networkx louvain_communities(seed=42) on the undirected graph).
    """
    distinct_subs = {p.get("subreddit") for p in posts if p.get("subreddit") and p.get("subreddit") != "unknown"}
    account_comm = {}

    if len(distinct_subs) > 1:
        # Multi-subreddit: node colour by subreddit
        user_subs = defaultdict(Counter)
        for p in posts:
            node_id = get_author_node(p)
            if p.get("subreddit"):
                user_subs[node_id][p["subreddit"]] += 1
        for u in ug.nodes():
            if user_subs[u]:
                account_comm[u] = user_subs[u].most_common(1)[0][0]
            else:
                account_comm[u] = next(iter(distinct_subs)) if distinct_subs else "unknown"
    else:
        # Single thread (one subreddit): use Louvain communities (seed=42)
        ug_undirected = ug.to_undirected()
        if ug_undirected.number_of_nodes() > 0 and ug_undirected.number_of_edges() > 0:
            comm_sets = list(louvain_communities(ug_undirected, seed=42))
            comm_sets.sort(key=lambda s: -len(s))
            for idx, cset in enumerate(comm_sets, start=1):
                label = f"Community {idx}"
                for node_id in cset:
                    account_comm[node_id] = label
        # Assign any remaining nodes
        for node_id in ug.nodes():
            if node_id not in account_comm:
                account_comm[node_id] = "Community 1"

    return account_comm

def users(posts, ug, account_comm, pr=None, root_name=None):
    by_user = defaultdict(list)
    for p in posts:
        by_user[get_author_node(p)].append(p)

    post_counts = [len(ps) for ps in by_user.values()]
    p95 = float(np.percentile(post_counts, 95)) if post_counts else 0.0
    if pr is None:
        pr = nx.pagerank(ug, weight="weight") if ug.number_of_nodes() else {}

    out = {}
    for u in ug.nodes():
        ps = by_user.get(u, [])
        n = len(ps)
        sent = int(ug.out_degree(u, weight="weight")) if ug.has_node(u) else 0
        rec = int(ug.in_degree(u, weight="weight")) if ug.has_node(u) else 0
        plat = ps[0].get("platform", "reddit") if ps else "reddit"

        eval_res = detector.evaluate_account(u, n, p95, sent, rec, plat)
        ts_list = [p["created_utc"] for p in ps if p.get("created_utc")]
        text_samples = [p["body"] for p in ps if p.get("body") and p["body"] not in ("[deleted]", "[removed]")]
        mis_scores = [detector.score_text_misinformation(t)["score"] for t in text_samples[:5]] if text_samples else []
        avg_mis = round(float(np.mean(mis_scores)), 2) if mis_scores else 0.15

        comm = account_comm.get(u, "Community 1")

        out[u] = {
            "name": u,
            "posts": n,
            "sent": sent,
            "received": rec,
            "influence": round(float(pr.get(u, 0)), 5),
            "community": comm,
            "bot_score": eval_res["bot_score"],
            "flagged": eval_res["flagged"],
            "signals": eval_res["signals"],
            "misinfo_score": avg_mis,
            "first": min(ts_list, default=None),
            "last": max(ts_list, default=None),
            "platform": plat
        }
    return out

def find_root(posts, topic, byid, root_post, usr=None):
    """
    Root detection with confidence:
    "confident" if the root submission row exists or one account has the
    earliest item with no other top-level item within 60 seconds;
    otherwise "ambiguous". Show the label and the reason.

    - Ignore AutoModerator (and any account flagged bot-likely) when choosing the
      earliest top-level item.
    - If the opening post is missing (e.g. Reddit comments only), label it
      "Earliest comment", not "Root Post". Keep confident/ambiguous.
    """
    clean_topic = clean_id(topic)
    post_type = "Root Post" if root_post else "Earliest comment"

    # 1. Root submission row exists
    if root_post:
        return {
            "id": clean_id(root_post["id"]),
            "author": root_post.get("author") or "[deleted]",
            "label": "confident",
            "post_type": post_type,
            "type": post_type,
            "reason": "Root submission row exists in dataset",
            "candidates": []
        }

    def is_bot_or_automod(p):
        auth = p.get("author") or "[deleted]"
        node_auth = get_author_node(p)
        if auth.lower() == "automoderator" or node_auth.lower() == "automoderator":
            return True
        if usr:
            if usr.get(auth, {}).get("flagged") or usr.get(node_auth, {}).get("flagged"):
                return True
        return False

    # 2. Check top-level items (ignore AutoModerator and bot-likely)
    top_items = []
    for p in posts:
        raw_p = p.get("parent_id") or p.get("parent") or ""
        clean_par = clean_id(raw_p) if raw_p else ""
        if not clean_par or clean_par == clean_topic or clean_par not in byid:
            if not is_bot_or_automod(p):
                top_items.append(p)

    if not top_items:
        non_bot_posts = [p for p in posts if not is_bot_or_automod(p)]
        earliest_all = min(non_bot_posts or posts, key=lambda x: x.get("created_utc", float('inf')), default=None)
        return {
            "id": clean_id(earliest_all["id"]) if earliest_all else clean_topic,
            "author": earliest_all.get("author", f"[root:{clean_topic}]") if earliest_all else f"[root:{clean_topic}]",
            "label": "ambiguous",
            "post_type": post_type,
            "type": post_type,
            "reason": "No top-level comments identified from non-bot accounts",
            "candidates": []
        }

    top_items.sort(key=lambda x: x.get("created_utc", 0))
    earliest = top_items[0]
    e_ts = earliest.get("created_utc", 0)
    e_author = earliest.get("author") or "[deleted]"

    # Check if another account has a top-level item within 60 seconds
    competing = [
        p for p in top_items
        if p != earliest
        and (p.get("author") or "[deleted]") != e_author
        and abs(p.get("created_utc", 0) - e_ts) <= 60
    ]

    item_noun = "comment" if not root_post else "item"

    if len(competing) == 0:
        return {
            "id": clean_id(earliest["id"]),
            "author": e_author,
            "label": "confident",
            "post_type": post_type,
            "type": post_type,
            "reason": f"One account ({e_author}) has the earliest {item_noun} with no other top-level {item_noun} within 60 seconds",
            "candidates": [{"id": clean_id(earliest["id"]), "author": e_author, "ts": e_ts}]
        }
    else:
        other_authors = list({p.get("author") or "[deleted]" for p in competing})
        return {
            "id": clean_id(earliest["id"]),
            "author": e_author,
            "label": "ambiguous",
            "post_type": post_type,
            "type": post_type,
            "reason": f"Multiple accounts ({', '.join([e_author] + other_authors[:2])}) posted top-level {item_noun}s within 60 seconds of earliest {item_noun}",
            "candidates": [{"id": clean_id(p["id"]), "author": p.get("author"), "ts": p.get("created_utc")} for p in [earliest] + competing[:4]]
        }

def trace(pid, byid, limit=50):
    """
    Hop-by-hop walk to the root with cycle detection as a Python loop (max 50 hops, stop on cycle).
    """
    path, seen = [], set()
    current = clean_id(pid)
    while current and current in byid and current not in seen and len(path) < limit:
        seen.add(current)
        post = byid[current]
        path.append(post)
        raw_p = post.get("parent_id") or post.get("parent")
        if not raw_p:
            break
        current = clean_id(raw_p)
    return path

def summary(posts, usr, root):
    """
    Summary line at the top, computed from the loaded thread:
    "Spread to N accounts across M communities in H hours, K flagged accounts"
    (H = first to last comment time).
    Count only communities with at least 5 accounts in the headline and badge.
    """
    all_ts = [p["created_utc"] for p in posts if p.get("created_utc")]
    if all_ts:
        span_s = max(all_ts) - min(all_ts)
        h_val = span_s / 3600.0
        H = round(h_val, 1)
        if H == int(H):
            H = int(H)
    else:
        H = 0

    N = len(usr)
    # Count only communities with at least 5 accounts
    comm_counts = Counter(u["community"] for u in usr.values() if u.get("community"))
    M = sum(1 for count in comm_counts.values() if count >= 5)
    K = sum(1 for u in usr.values() if u.get("flagged"))
    high_risk = sum(1 for u in usr.values() if u.get("misinfo_score", 0) >= 0.5 or u.get("flagged"))

    headline = f"Spread to {N} accounts across {M} communities in {H} hours, {K} flagged accounts"

    return {
        "headline": headline,
        "accounts": N,
        "communities": M,
        "flagged": K,
        "high_risk": high_risk,
        "posts": len(posts),
        "duration_hours": H,
        "root": root
    }
