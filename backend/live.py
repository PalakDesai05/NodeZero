"""Real-time fetch and keyword search for Bluesky and Mastodon."""
import os
import re
import time
import requests
from datetime import datetime
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

USER_AGENT = "NodeZero-research/1.0"
H = {"User-Agent": USER_AGENT}

_bsky_session = None
_bsky_session_time = 0

SEARCH_CACHE = {}         # query -> {"timestamp": float, "result": dict}
PROGRESS_STATUS = {}      # query -> {"text": str, "done": bool, "error": str, "result": dict}

def get_bsky_token():
    """Authenticates with Bluesky if credentials are set; refreshes token every 30 mins."""
    global _bsky_session, _bsky_session_time
    handle = os.getenv("BSKY_HANDLE")
    pw = os.getenv("BSKY_APP_PASSWORD")
    if not handle or not pw:
        return None

    now = time.time()
    if _bsky_session and (now - _bsky_session_time < 1800):
        return _bsky_session

    try:
        r = requests.post(
            "https://bsky.social/xrpc/com.atproto.server.createSession",
            json={"identifier": handle, "password": pw},
            headers=H,
            timeout=10
        )
        if r.ok:
            _bsky_session = r.json().get("accessJwt")
            _bsky_session_time = now
            return _bsky_session
    except Exception:
        pass
    return None

def bsky_headers():
    tok = get_bsky_token()
    if tok:
        return {**H, "Authorization": f"Bearer {tok}"}
    return H

def mastodon_headers():
    tok = os.getenv("MASTODON_TOKEN")
    if tok and not tok.startswith("urn:"):
        return {**H, "Authorization": f"Bearer {tok}"}
    return H

def parse_iso_ts(s):
    if not s:
        return 0.0
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0

def strip_html(html_text):
    if not html_text:
        return ""
    # Remove HTML tags and unescape common entities
    text = re.sub(r"<[^>]+>", " ", str(html_text))
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    return " ".join(text.split())

# -------------------------------------------------------------
# Bluesky Post Link Fetch
# -------------------------------------------------------------
def fetch_bluesky_link(url):
    m = re.search(r"profile/([^/]+)/post/([^/?#]+)", url)
    if not m:
        raise ValueError("Expected https://bsky.app/profile/<handle>/post/<id>")
    handle, rkey = m.groups()

    # 1. Resolve handle
    did = handle
    if not handle.startswith("did:"):
        resolve_url = "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle"
        try:
            r = requests.get(resolve_url, params={"handle": handle}, headers=H, timeout=20)
            if r.status_code in (401, 403):
                r = requests.get("https://bsky.social/xrpc/com.atproto.identity.resolveHandle",
                                 params={"handle": handle}, headers=bsky_headers(), timeout=20)
            r.raise_for_status()
            did = r.json().get("did", handle)
        except Exception as e:
            raise ValueError(f"Could not resolve Bluesky handle '{handle}': {e}")

    # 2. Get post thread
    thread_uri = f"at://{did}/app.bsky.feed.post/{rkey}"
    thread_url = "https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread"
    try:
        r = requests.get(thread_url, params={"uri": thread_uri, "depth": 1000, "parentHeight": 0},
                         headers=H, timeout=20)
        if r.status_code in (401, 403):
            r = requests.get("https://bsky.social/xrpc/app.bsky.feed.getPostThread",
                             params={"uri": thread_uri, "depth": 1000, "parentHeight": 0},
                             headers=bsky_headers(), timeout=20)
        r.raise_for_status()
        thread_data = r.json().get("thread", {})
    except Exception as e:
        raise ValueError(f"Could not fetch Bluesky thread: {e}")

    topic = f"bsky:{rkey}"
    rows = []
    seen_ids = set()

    def process_node(node, parent_uri=None):
        if len(rows) >= 2000:
            return
        if not node or "post" not in node:
            return
        p = node["post"]
        uri = p.get("uri")
        if not uri or uri in seen_ids:
            return
        seen_ids.add(uri)

        rec = p.get("record", {})
        author_handle = p.get("author", {}).get("handle", "unknown")
        created_at = parse_iso_ts(rec.get("createdAt") or p.get("indexedAt"))
        text = rec.get("text", "")
        domain_comm = ".".join(author_handle.split(".")[-2:]) if "." in author_handle else "bluesky"

        # Determine explicit parent URI
        p_uri = parent_uri
        reply_meta = rec.get("reply") or {}
        if not p_uri and reply_meta.get("parent"):
            p_uri = reply_meta["parent"].get("uri")

        rows.append({
            "id": uri,
            "author": author_handle,
            "parent_id": p_uri,
            "parent": p_uri,
            "root_id": thread_uri,
            "body": text,
            "created_utc": created_at,
            "ts": created_at,
            "subreddit": "bluesky",
            "community": domain_comm,
            "platform": "bluesky",
            "topic": topic
        })

        # Process replies (cap at 2000 total rows)
        for child in node.get("replies", []):
            if len(rows) >= 2000:
                break
            process_node(child, parent_uri=uri)

    # Process ancestors if present
    ancestors = []
    curr = thread_data
    while curr.get("parent"):
        curr = curr["parent"]
        ancestors.append(curr)
    for a in reversed(ancestors):
        process_node(a)

    # Process main thread and descendants
    process_node(thread_data, parent_uri=None)
    return topic, rows

# -------------------------------------------------------------
# Mastodon Post Link Fetch
# -------------------------------------------------------------
def fetch_mastodon_link(url):
    host = urlparse(url).netloc
    m = re.search(r"/(\d{5,})/?(?:[?#].*)?$", url)
    if not m:
        raise ValueError("Expected https://<instance>/@user/<status id>")
    sid = m.group(1)

    hdrs = mastodon_headers()
    api_status = f"https://{host}/api/v1/statuses/{sid}"
    api_context = f"https://{host}/api/v1/statuses/{sid}/context"

    try:
        r_status = requests.get(api_status, headers=hdrs, timeout=20)
        if r_status.status_code in (401, 404):
            raise ValueError("use the post's own server link or set MASTODON_TOKEN")
        r_status.raise_for_status()
        status_obj = r_status.json()

        r_context = requests.get(api_context, headers=hdrs, timeout=20)
        r_context.raise_for_status()
        context_obj = r_context.json()
    except ValueError:
        raise
    except requests.exceptions.HTTPError as he:
        if he.response is not None and he.response.status_code in (401, 404):
            raise ValueError("use the post's own server link or set MASTODON_TOKEN")
        raise ValueError(f"Mastodon HTTP error: {he}")
    except Exception as e:
        raise ValueError(f"Could not fetch Mastodon status: {e}")

    topic = f"masto:{host}:{sid}"
    root_id = f"{host}:{sid}"
    rows = []
    seen_ids = set()

    all_statuses = context_obj.get("ancestors", []) + [status_obj] + context_obj.get("descendants", [])

    for s in all_statuses:
        item_id = str(s.get("id"))
        uid = f"{host}:{item_id}"
        if uid in seen_ids:
            continue
        seen_ids.add(uid)

        acct = s.get("account", {}).get("acct", "unknown")
        author_name = acct if "@" in acct else f"{acct}@{host}"
        in_reply_to = s.get("in_reply_to_id")
        p_id = f"{host}:{in_reply_to}" if in_reply_to else None
        if item_id == sid:
            p_id = None  # Root post has no parent in this thread

        text = strip_html(s.get("content", ""))
        created_at = parse_iso_ts(s.get("created_at"))
        comm = author_name.split("@")[-1] if "@" in author_name else host

        rows.append({
            "id": uid,
            "author": author_name,
            "parent_id": p_id,
            "parent": p_id,
            "root_id": root_id,
            "body": text,
            "created_utc": created_at,
            "ts": created_at,
            "subreddit": host,
            "community": comm,
            "platform": "mastodon",
            "topic": topic
        })

    return topic, rows

def fetch_link(url):
    """Fetches post link for Bluesky or Mastodon."""
    clean_u = url.strip()
    if "bsky.app" in clean_u:
        return fetch_bluesky_link(clean_u)
    return fetch_mastodon_link(clean_u)

# -------------------------------------------------------------
# Keyword Search Across Bluesky and Mastodon
# -------------------------------------------------------------
def search_bluesky(keywords: str, deadline: float):
    """
    Bluesky keyword search:
    - Search top 25 posts, filter replyCount >= 5, keep top 5 posts.
    - Fallback: retry with 3, 2, 1 longest words if nothing found.
    - For each top post, fetch thread with getPostThread and flatten replies.
    """
    token = get_bsky_token()
    if not token:
        return [], "Set BSKY_HANDLE and BSKY_APP_PASSWORD in .env for Bluesky search."

    headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    words = [re.sub(r"[^a-zA-Z0-9]", "", w) for w in keywords.split()]
    words = [w for w in words if w]
    sorted_words = sorted(words, key=len, reverse=True)

    search_queries = [keywords]
    if len(sorted_words) >= 3:
        search_queries.append(" ".join(sorted_words[:3]))
    if len(sorted_words) >= 2:
        search_queries.append(" ".join(sorted_words[:2]))
    if len(sorted_words) >= 1 and sorted_words[0] != keywords:
        search_queries.append(sorted_words[0])

    posts = []
    for q in search_queries:
        if time.time() > deadline:
            break
        try:
            r = requests.get(
                "https://bsky.social/xrpc/app.bsky.feed.searchPosts",
                params={"q": q, "sort": "top", "limit": 25},
                headers=headers,
                timeout=10
            )
            if r.ok:
                found = r.json().get("posts", [])
                if found:
                    posts = found
                    break
        except Exception:
            continue

    # Keep top 5 posts with most replies (replyCount >= 5)
    candidates = [p for p in posts if p.get("replyCount", 0) >= 5]
    candidates.sort(key=lambda x: x.get("replyCount", 0), reverse=True)
    top5 = candidates[:5]

    threads = []
    for p in top5:
        if time.time() > deadline:
            break
        uri = p.get("uri")
        if not uri:
            continue
        try:
            r = requests.get(
                "https://bsky.social/xrpc/app.bsky.feed.getPostThread",
                params={"uri": uri, "depth": 1000, "parentHeight": 0},
                headers=headers,
                timeout=10
            )
            if not r.ok:
                continue
            thread_data = r.json().get("thread", {})
            rkey = uri.split("/")[-1]
            topic = f"search:bsky:{rkey}"
            rows = []
            seen_ids = set()

            def process_node(node, p_uri=None):
                if len(rows) >= 2000 or not node or "post" not in node:
                    return
                p_obj = node["post"]
                p_uri_val = p_obj.get("uri")
                if not p_uri_val or p_uri_val in seen_ids:
                    return
                seen_ids.add(p_uri_val)
                rec = p_obj.get("record", {})
                h = p_obj.get("author", {}).get("handle", "unknown")
                txt = rec.get("text", "")
                cat = parse_iso_ts(rec.get("createdAt") or p_obj.get("indexedAt"))
                comm = ".".join(h.split(".")[-2:]) if "." in h else "bluesky"
                rows.append({
                    "id": p_uri_val,
                    "author": h,
                    "parent_id": p_uri,
                    "parent": p_uri,
                    "root_id": uri,
                    "body": txt,
                    "created_utc": cat,
                    "ts": cat,
                    "subreddit": "bluesky",
                    "community": comm,
                    "platform": "bluesky",
                    "topic": topic
                })
                for c in node.get("replies", []):
                    process_node(c, p_uri=p_uri_val)

            process_node(thread_data, p_uri=None)
            if rows:
                root_text = rows[0]["body"]
                first_50 = (root_text[:50].replace("\n", " ") if root_text else f"Bluesky post {rkey}").strip()
                replies_count = max(0, len(rows) - 1)
                threads.append({
                    "topic": topic,
                    "platform": "bluesky",
                    "rows": rows,
                    "first_50": first_50,
                    "replies_count": replies_count,
                    "label": f"{first_50} - bluesky - {replies_count} replies"
                })
        except Exception:
            continue

    return threads, None

def search_mastodon(keywords: str, deadline: float):
    """
    Mastodon keyword search:
    - Search tags on mastodon.social for each keyword (letters/digits only).
    - If MASTODON_TOKEN is set, also query /api/v2/search.
    - Keep statuses with replies_count >= 3.
    - Fetch /context for each status and flatten descendants.
    """
    headers = mastodon_headers()
    words = [re.sub(r"[^a-zA-Z0-9]", "", w).lower() for w in keywords.split()]
    words = [w for w in words if w]

    statuses = []
    seen_status_ids = set()

    for word in words:
        if time.time() > deadline:
            break
        try:
            r = requests.get(
                f"https://mastodon.social/api/v1/timelines/tag/{word}",
                params={"limit": 40},
                headers=headers,
                timeout=10
            )
            if r.ok:
                for s in r.json():
                    sid = str(s.get("id"))
                    if sid not in seen_status_ids:
                        seen_status_ids.add(sid)
                        statuses.append(s)
        except Exception:
            pass

    # Optional /api/v2/search if MASTODON_TOKEN is valid
    tok = os.getenv("MASTODON_TOKEN")
    if tok and not tok.startswith("urn:") and time.time() <= deadline:
        try:
            r = requests.get(
                "https://mastodon.social/api/v2/search",
                params={"q": keywords, "type": "statuses", "resolve": "false"},
                headers=headers,
                timeout=10
            )
            if r.ok:
                for s in r.json().get("statuses", []):
                    sid = str(s.get("id"))
                    if sid not in seen_status_ids:
                        seen_status_ids.add(sid)
                        statuses.append(s)
        except Exception:
            pass

    # Keep statuses with replies_count >= 3
    candidates = [s for s in statuses if s.get("replies_count", 0) >= 3]
    candidates.sort(key=lambda s: s.get("replies_count", 0), reverse=True)
    top5 = candidates[:5]

    host = "mastodon.social"
    threads = []
    for s in top5:
        if time.time() > deadline:
            break
        sid = str(s.get("id"))
        try:
            r_ctx = requests.get(f"https://{host}/api/v1/statuses/{sid}/context", headers=headers, timeout=10)
            if not r_ctx.ok:
                continue
            ctx = r_ctx.json()
            topic = f"search:masto:{host}:{sid}"
            root_id = f"{host}:{sid}"
            rows = []
            seen_ids = set()

            all_s = [s] + ctx.get("descendants", [])
            for item in all_s:
                iid = str(item.get("id"))
                uid = f"{host}:{iid}"
                if uid in seen_ids:
                    continue
                seen_ids.add(uid)
                acct = item.get("account", {}).get("acct", "unknown")
                author_name = acct if "@" in acct else f"{acct}@{host}"
                in_reply = item.get("in_reply_to_id")
                p_id = f"{host}:{in_reply}" if in_reply and iid != sid else None
                body = strip_html(item.get("content", ""))
                cat = parse_iso_ts(item.get("created_at"))
                comm = author_name.split("@")[-1] if "@" in author_name else host

                rows.append({
                    "id": uid,
                    "author": author_name,
                    "parent_id": p_id,
                    "parent": p_id,
                    "root_id": root_id,
                    "body": body,
                    "created_utc": cat,
                    "ts": cat,
                    "subreddit": host,
                    "community": comm,
                    "platform": "mastodon",
                    "topic": topic
                })

            if rows:
                root_text = rows[0]["body"]
                first_50 = (root_text[:50].replace("\n", " ") if root_text else f"Mastodon status {sid}").strip()
                replies_count = max(0, len(rows) - 1)
                threads.append({
                    "topic": topic,
                    "platform": "mastodon",
                    "rows": rows,
                    "first_50": first_50,
                    "replies_count": replies_count,
                    "label": f"{first_50} - mastodon - {replies_count} replies"
                })
        except Exception:
            continue

    return threads, None

def deduplicate_threads(threads):
    """
    Deduplication rules:
    - Remove duplicates by id.
    - If the same long text (40+ characters) appears on both platforms,
      keep one copy and re-point its replies to it.
    """
    seen_ids = set()
    text_to_canonical = {}  # clean_text -> {"id": canonical_id, "platform": platform}
    remap_ids = {}          # duplicate_id -> canonical_id

    # First pass: map long texts across platforms
    for th in threads:
        for r in th["rows"]:
            body_clean = " ".join(r["body"].strip().lower().split())
            if len(body_clean) >= 40:
                if body_clean in text_to_canonical:
                    canonical = text_to_canonical[body_clean]
                    if canonical["platform"] != r["platform"]:
                        remap_ids[r["id"]] = canonical["id"]
                else:
                    text_to_canonical[body_clean] = {"id": r["id"], "platform": r["platform"]}

    # Second pass: remove duplicates and re-point replies
    deduped_threads = []
    for th in threads:
        cleaned_rows = []
        for r in th["rows"]:
            # If this row is a duplicate of a canonical post from another platform, omit it
            if r["id"] in remap_ids:
                continue
            if r["id"] in seen_ids:
                continue
            seen_ids.add(r["id"])

            # Re-point replies if parent was remapped
            if r["parent_id"] in remap_ids:
                r["parent_id"] = remap_ids[r["parent_id"]]
                r["parent"] = r["parent_id"]
            if r["root_id"] in remap_ids:
                r["root_id"] = remap_ids[r["root_id"]]

            cleaned_rows.append(r)

        if cleaned_rows:
            th["rows"] = cleaned_rows
            th["replies_count"] = max(0, len(cleaned_rows) - 1)
            th["label"] = f"{th['first_50']} - {th['platform']} - {th['replies_count']} replies"
            deduped_threads.append(th)

    return deduped_threads

def search_live_keywords(keywords: str):
    """
    Coordinates keyword search across Bluesky and Mastodon.
    Runs within a 30-second overall timeout.
    Caches results for 10 minutes (600s).
    """
    clean_k = keywords.strip().lower()
    now = time.time()

    # Check 10-minute cache
    if clean_k in SEARCH_CACHE:
        entry = SEARCH_CACHE[clean_k]
        if now - entry["timestamp"] < 600:
            return entry["result"]

    deadline = now + 30.0
    PROGRESS_STATUS[clean_k] = {"text": "Searching Bluesky and Mastodon...", "done": False, "error": "", "result": None}

    import store

    bsky_threads, bsky_err = search_bluesky(keywords, deadline)
    PROGRESS_STATUS[clean_k]["text"] = f"Bluesky found {len(bsky_threads)} threads. Searching Mastodon..."

    masto_threads, masto_err = search_mastodon(keywords, deadline)
    PROGRESS_STATUS[clean_k]["text"] = "Deduplicating threads..."

    all_threads = bsky_threads + masto_threads
    final_threads = deduplicate_threads(all_threads)

    # Save to store
    for th in final_threads:
        store.save_search_thread(th["topic"], th["rows"], {
            "platform": th["platform"],
            "label": th["label"],
            "first_50": th["first_50"],
            "replies": th["replies_count"],
            "query": keywords
        })

    count_bsky = sum(1 for t in final_threads if t["platform"] == "bluesky")
    count_masto = sum(1 for t in final_threads if t["platform"] == "mastodon")
    total_found = len(final_threads)

    # Sort to find the biggest thread (most comments/rows)
    biggest_topic = None
    if final_threads:
        biggest = max(final_threads, key=lambda t: len(t["rows"]))
        biggest_topic = biggest["topic"]

    if total_found > 0:
        summary_msg = f"Found {total_found} threads (Bluesky {count_bsky}, Mastodon {count_masto})"
    else:
        summary_msg = "Nothing found, try fewer words"

    warnings = []
    if bsky_err:
        warnings.append(bsky_err)
    if masto_err:
        warnings.append(masto_err)

    result = {
        "type": "search",
        "query": keywords,
        "message": summary_msg,
        "warning": " ".join(warnings) if warnings else None,
        "counts": {
            "bluesky": count_bsky,
            "mastodon": count_masto,
            "total": total_found
        },
        "biggest_topic": biggest_topic,
        "threads": [
            {
                "topic": t["topic"],
                "platform": t["platform"],
                "comments": len(t["rows"]),
                "posts": len(t["rows"]),
                "label": t["label"]
            }
            for t in final_threads
        ]
    }

    SEARCH_CACHE[clean_k] = {
        "timestamp": now,
        "result": result
    }
    PROGRESS_STATUS[clean_k] = {
        "text": summary_msg,
        "done": True,
        "error": "",
        "result": result
    }

    return result

def fetch(input_text: str):
    """
    Main dispatch:
    If text starts with http, fetch that post link as now.
    Otherwise treat it as keywords and search.
    """
    t = input_text.strip()
    if t.lower().startswith("http://") or t.lower().startswith("https://"):
        topic, rows = fetch_link(t)
        return {
            "type": "link",
            "topic": topic,
            "posts": len(rows),
            "rows": rows
        }
    else:
        return search_live_keywords(t)
