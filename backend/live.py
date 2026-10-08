"""Real-time fetch and keyword search for Reddit (2019), Bluesky and Mastodon (R1, R2, R5)."""
import os
import re
import time
import requests
from datetime import datetime
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

import search_validator as sv
import query_planner as qp

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

USER_AGENT = "NodeZero-research/1.0"
H = {"User-Agent": USER_AGENT}

_bsky_session = None
_bsky_session_time = 0

SEARCH_CACHE = {}         # query -> {"timestamp": float, "result": dict}
PROGRESS_STATUS = {}      # query -> {"text": str, "done": bool, "error": str, "result": dict}

# Note: mstdn.social was removed because its public timeline API endpoint (/api/v1/timelines/tag/{tag})
# returns HTTP 422 with {"error":"This method requires an authenticated user"}, requiring an instance-specific token.
MASTODON_INSTANCES = ["mastodon.social", "mastodon.online"]

def classify_thread_match(thread_rows, query: str, query_plan: dict = None) -> tuple[int, str]:
    """
    Computes match quality tier and label for a thread:
    - Tier 1: [Exact phrase] (exact phrase matches in thread text)
    - Tier 2: [All keywords] (all query keywords match)
    - Tier 3: [Partial: X of Y] (at least 2 keywords match)
    - Tier 4: [Weak: 1 keyword] (only 1 keyword matches)
    """
    if not thread_rows:
        return 4, "[Weak: 1 keyword]"

    from search_validator import STOPWORDS
    clean_q = (query or "").strip().lower().replace("#", "")
    all_text = " ".join((r.get("body") or "") for r in thread_rows).lower()

    if query_plan and query_plan.get("keywords"):
        raw_kws = query_plan["keywords"]
    else:
        raw_kws = re.findall(r"\b\w+\b", clean_q)

    keywords = [
        k.lower().strip() for k in raw_kws
        if k.lower().strip() not in STOPWORDS and len(k.strip()) >= 2
    ]
    dedup = []
    for k in keywords:
        if k not in dedup:
            dedup.append(k)
    keywords = dedup
    total_kw = len(keywords)

    # Check exact phrase with \b boundaries
    phrase_pattern = r"\b" + re.escape(clean_q) + r"\b"
    if clean_q and re.search(phrase_pattern, all_text):
        return 1, "[Exact phrase]"

    # Check whole word keywords
    matched = 0
    for kw in keywords:
        if re.search(r"\b" + re.escape(kw) + r"\b", all_text):
            matched += 1

    if total_kw > 0 and matched == total_kw:
        return 2, "[All keywords]"
    elif matched >= 2:
        return 3, f"[Partial: {matched} of {total_kw}]"
    else:
        return 4, "[Weak: 1 keyword]"

def get_bsky_token(force_refresh: bool = False):
    """Authenticates with Bluesky if credentials are set; refreshes token every 30 mins or when requested."""
    global _bsky_session, _bsky_session_time
    handle = os.getenv("BSKY_HANDLE")
    pw = os.getenv("BSKY_APP_PASSWORD")
    if not handle or not pw:
        return None

    now = time.time()
    if not force_refresh and _bsky_session and (now - _bsky_session_time < 1800):
        return _bsky_session

    try:
        r = requests.post(
            "https://bsky.social/xrpc/com.atproto.server.createSession",
            json={"identifier": handle, "password": pw},
            headers=H,
            timeout=8
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
            r = requests.get(resolve_url, params={"handle": handle}, headers=H, timeout=10)
            if r.status_code in (401, 403):
                r = requests.get("https://bsky.social/xrpc/com.atproto.identity.resolveHandle",
                                 params={"handle": handle}, headers=bsky_headers(), timeout=10)
            r.raise_for_status()
            did = r.json().get("did", handle)
        except Exception as e:
            raise ValueError(f"Could not resolve Bluesky handle '{handle}': {e}")

    # 2. Get post thread
    thread_uri = f"at://{did}/app.bsky.feed.post/{rkey}"
    thread_url = "https://bsky.social/xrpc/app.bsky.feed.getPostThread" if get_bsky_token() else "https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread"
    try:
        r = requests.get(thread_url, params={"uri": thread_uri, "depth": 1000, "parentHeight": 0},
                         headers=bsky_headers(), timeout=12)
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

        for child in node.get("replies", []):
            if len(rows) >= 2000:
                break
            process_node(child, parent_uri=uri)

    ancestors = []
    curr = thread_data
    while curr.get("parent"):
        curr = curr["parent"]
        ancestors.append(curr)
    for a in reversed(ancestors):
        process_node(a)

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
        r_status = requests.get(api_status, headers=hdrs, timeout=10)
        if r_status.status_code in (401, 404):
            raise ValueError("use the post's own server link or set MASTODON_TOKEN")
        r_status.raise_for_status()
        status_obj = r_status.json()

        r_context = requests.get(api_context, headers=hdrs, timeout=10)
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
            p_id = None

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
# Bluesky Search with Query Planner, Retries, Parallelism (R5.2)
# -------------------------------------------------------------
def _bsky_search_call(query: str, sort: str, cursor: str = None):
    """Calls Bluesky searchPosts with session refresh on 401/403 and backoff on 429/5xx."""
    headers = bsky_headers()
    api_url = "https://bsky.social/xrpc/app.bsky.feed.searchPosts" if get_bsky_token() else "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"

    params = {"q": query, "sort": sort, "limit": 25}
    if cursor:
        params["cursor"] = cursor

    retries = 2
    for attempt in range(retries + 1):
        try:
            r = requests.get(api_url, params=params, headers=headers, timeout=8)
            if r.status_code in (401, 403):
                # Refresh session once
                new_token = get_bsky_token(force_refresh=True)
                if new_token:
                    headers = {**H, "Authorization": f"Bearer {new_token}"}
                    r = requests.get(api_url, params=params, headers=headers, timeout=8)

            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue

            return r.status_code, (r.json() if r.ok else {}), None
        except requests.exceptions.Timeout:
            if attempt == retries:
                return 408, {}, "timeout"
            time.sleep(0.5)
        except requests.exceptions.RequestException as e:
            if attempt == retries:
                return 500, {}, str(e)
            time.sleep(0.5)

    return 500, {}, "network error"

def _fetch_bsky_single_thread(candidate_post, headers, query_plan: dict = None):
    """Worker function to fetch and flatten a single Bluesky thread."""
    uri = candidate_post.get("uri")
    if not uri:
        return None
    api_url = "https://bsky.social/xrpc/app.bsky.feed.getPostThread" if get_bsky_token() else "https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread"

    retries = 2
    thread_data = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(api_url, params={"uri": uri, "depth": 100, "parentHeight": 0}, headers=headers, timeout=8)
            if r.status_code in (401, 403):
                new_tok = get_bsky_token(force_refresh=True)
                if new_tok:
                    headers = {**H, "Authorization": f"Bearer {new_tok}"}
                    r = requests.get(api_url, params={"uri": uri, "depth": 100, "parentHeight": 0}, headers=headers, timeout=8)
            if r.status_code in (429, 500, 502, 503) and attempt < retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            if r.ok:
                thread_data = r.json().get("thread", {})
                break
        except Exception:
            if attempt == retries:
                break
            time.sleep(0.5)

    rkey = uri.split("/")[-1]
    topic = f"search:bsky:{rkey}"

    if not thread_data or "post" not in thread_data:
        # Fallback to single-post representation if thread fetch failed or no replies
        rec = candidate_post.get("record", {})
        h = candidate_post.get("author", {}).get("handle", "unknown")
        txt = rec.get("text", "")
        cat = parse_iso_ts(rec.get("createdAt") or candidate_post.get("indexedAt"))
        comm = ".".join(h.split(".")[-2:]) if "." in h else "bluesky"
        rows = [{
            "id": uri,
            "author": h,
            "parent_id": None,
            "parent": None,
            "root_id": uri,
            "body": txt,
            "created_utc": cat,
            "ts": cat,
            "subreddit": "bluesky",
            "community": comm,
            "platform": "Bluesky",
            "topic": topic
        }]
    else:
        rows = []
        seen_ids = set()

        def process_node(node, p_uri=None):
            if len(rows) >= 500 or not node or "post" not in node:
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
                "platform": "Bluesky",
                "topic": topic
            })
            for c in node.get("replies", []):
                process_node(c, p_uri=p_uri_val)

        process_node(thread_data, p_uri=None)

    if not rows:
        return None

    root_text = rows[0]["body"]
    first_50 = (root_text[:50].replace("\n", " ") if root_text else f"Bluesky post {rkey}").strip()
    replies_count = max(0, len(rows) - 1)
    label_suffix = f"{replies_count} replies" if replies_count > 0 else "no replies yet"

    tier, q_label = classify_thread_match(rows, query_plan.get("raw", "") if query_plan else "", query_plan)

    return {
        "topic": topic,
        "platform": "Bluesky",
        "rows": rows,
        "first_50": first_50,
        "replies_count": replies_count,
        "quality_tier": tier,
        "quality_label": q_label,
        "label": f"{q_label} {first_50} - Bluesky - {label_suffix}",
        "relevance": candidate_post.get("replyCount", 0) + (10 if replies_count > 0 else 1)
    }

def search_bluesky(query_plan: dict, deadline: float):
    """
    Bluesky keyword search (R5.2):
    - Run ALL variants until at least 5 candidate threads or deadline.
    - searchPosts with sort=top and sort=latest, 3 pages each with cursor, merged by URI.
    - Adaptive candidate threshold: replyCount >= 5, else >= 2, else >= 1, else top single posts.
    - Parallel thread fetching with max 4 workers.
    - Diagnostics tracking per variant and source status.
    """
    variants = query_plan.get("variants", [])
    if not variants:
        variants = [query_plan.get("raw", "")]

    all_posts_by_uri = {}
    variant_diagnostics = []
    has_credentials = bool(os.getenv("BSKY_HANDLE") and os.getenv("BSKY_APP_PASSWORD"))
    last_status = 200
    overall_err = ""

    for variant in variants:
        if time.time() > deadline:
            break

        # Search top and latest, up to 3 pages each
        variant_posts = []
        variant_status = 200
        variant_err = None

        for sort_mode in ("top", "latest"):
            cursor = None
            for page in range(3):
                if time.time() > deadline:
                    break
                status, data, err = _bsky_search_call(variant, sort=sort_mode, cursor=cursor)
                variant_status = status
                if err:
                    variant_err = err
                posts = data.get("posts", [])
                for p in posts:
                    uri = p.get("uri")
                    if uri and uri not in all_posts_by_uri:
                        all_posts_by_uri[uri] = p
                        variant_posts.append(p)

                cursor = data.get("cursor")
                if not cursor or len(posts) == 0:
                    break

        variant_diagnostics.append({
            "source": "bluesky",
            "variant": variant,
            "status_code": variant_status,
            "posts_found": len(variant_posts),
            "threads_kept": 0,
            "error": variant_err
        })
        last_status = variant_status

        # Stop once at least 5 candidate threads (replyCount >= 5) are collected (R5.2)
        top_candidates = [p for p in all_posts_by_uri.values() if p.get("replyCount", 0) >= 5]
        if len(top_candidates) >= 5:
            break

    total_posts = list(all_posts_by_uri.values())

    # Adaptive candidate threshold
    candidates = [p for p in total_posts if p.get("replyCount", 0) >= 5]
    if len(candidates) < 5:
        candidates = [p for p in total_posts if p.get("replyCount", 0) >= 2]
    if len(candidates) < 5:
        candidates = [p for p in total_posts if p.get("replyCount", 0) >= 1]
    if not candidates and total_posts:
        # Fallback to single-post results labelled "no replies yet"
        candidates = total_posts[:5]

    candidates.sort(key=lambda x: x.get("replyCount", 0), reverse=True)
    top_candidates = candidates[:5]

    # Parallel thread fetching with ThreadPoolExecutor (max 4 workers)
    threads = []
    if top_candidates:
        hdrs = bsky_headers()
        with ThreadPoolExecutor(max_workers=min(4, len(top_candidates))) as executor:
            future_to_cand = {
                executor.submit(_fetch_bsky_single_thread, cand, hdrs, query_plan): cand
                for cand in top_candidates
            }
            for future in as_completed(future_to_cand):
                try:
                    res = future.result()
                    if res:
                        threads.append(res)
                except Exception:
                    pass

    # Determine diagnostic classification
    if not has_credentials:
        source_status = "credentials missing"
    elif last_status == 429:
        source_status = "rate limited"
    elif last_status >= 500 or (last_status != 200 and len(total_posts) == 0):
        source_status = "network error"
    elif len(total_posts) == 0:
        source_status = "nothing exists for this topic"
    else:
        source_status = "ok"

    # Update variant diagnostics threads_kept on matching variants
    for diag in variant_diagnostics:
        if diag["posts_found"] > 0:
            diag["threads_kept"] = len(threads)

    diagnostic_summary = {
        "status": source_status,
        "status_code": last_status,
        "posts_found": len(total_posts),
        "threads_kept": len(threads),
        "message": f"Bluesky: {len(threads)} threads kept from {len(total_posts)} posts found"
    }

    return threads, diagnostic_summary, variant_diagnostics

# -------------------------------------------------------------
# Mastodon Search with Multi-instance Pagination & Adaptive Threshold (R5.3)
# -------------------------------------------------------------
def search_mastodon(query_plan: dict, deadline: float):
    """
    Mastodon keyword search (R5.3, R4):
    - Clean hashtag list (remove stopwords and pure numbers, skip 1-2 letter hashtags).
    - Run all instance/hashtag requests in parallel (ThreadPoolExecutor, max 8) with a 5-second timeout each.
    - Share one overall 25-second deadline.
    - Order the work: specific hashtags first.
    - Posts with zero replies are shown as "no replies yet" single-post results, not dropped.
    """
    mastodon_deadline = min(deadline, time.time() + 25.0)

    keywords = query_plan.get("keywords", [])
    raw = query_plan.get("raw", "")
    synonyms = query_plan.get("synonyms", [])

    # Clean hashtag candidates: letters/numbers, not pure digits, not stopwords, length > 2
    tags = []
    compound_tags = []

    # 1. Specific compound tag first (e.g., #odicricket, #100runsodi, #nepalflood)
    if len(keywords) >= 2:
        compound = "".join(re.sub(r"[^a-zA-Z0-9]", "", k).lower() for k in keywords[:3])
        if compound and not compound.isdigit() and compound not in sv.STOPWORDS and len(compound) > 2:
            compound_tags.append(compound)

    for kw in keywords:
        clean_tag = re.sub(r"[^a-zA-Z0-9]", "", kw).lower()
        if clean_tag and not clean_tag.isdigit() and clean_tag not in sv.STOPWORDS and len(clean_tag) > 2:
            if clean_tag not in tags and clean_tag not in compound_tags:
                tags.append(clean_tag)
        for w in kw.split():
            cw = re.sub(r"[^a-zA-Z0-9]", "", w).lower()
            if cw and not cw.isdigit() and cw not in sv.STOPWORDS and len(cw) > 2:
                if cw not in tags and cw not in compound_tags:
                    tags.append(cw)

    for syn in synonyms[:2]:
        csyn = re.sub(r"[^a-zA-Z0-9]", "", syn).lower()
        if csyn and not csyn.isdigit() and csyn not in sv.STOPWORDS and len(csyn) > 2:
            if csyn not in tags and csyn not in compound_tags:
                tags.append(csyn)

    if not tags and not compound_tags and raw:
        clean_raw = re.sub(r"[^a-zA-Z0-9]", "", raw).lower()
        if clean_raw and not clean_raw.isdigit() and clean_raw not in sv.STOPWORDS and len(clean_raw) > 2:
            tags.append(clean_raw)

    # Order work: specific hashtags first (compound tags first, then longer tags)
    all_ordered_tags = compound_tags + sorted(tags, key=len, reverse=True)

    planned_targets = []
    for tag in all_ordered_tags:
        for host in MASTODON_INSTANCES:
            planned_targets.append((host, tag))

    statuses_by_uid = {}
    variant_diagnostics = []
    hdrs = mastodon_headers()

    def _query_target(target):
        host, tag = target
        url = f"https://{host}/api/v1/timelines/tag/{tag}"
        posts = []
        err_msg = None
        status_code = 200
        time_left = max(0.5, mastodon_deadline - time.time())
        req_timeout = min(5.0, time_left)

        try:
            r = requests.get(url, params={"limit": 40}, headers=hdrs, timeout=req_timeout)
            status_code = r.status_code
            if r.ok:
                items = r.json()
                if isinstance(items, list):
                    for s in items:
                        s["_host"] = host
                        posts.append(s)
            else:
                err_msg = f"HTTP {status_code}"
        except requests.exceptions.Timeout:
            status_code = 408
            err_msg = "Request timed out"
        except Exception as e:
            status_code = 500
            err_msg = str(e)

        if err_msg:
            status_label = "FAILED"
        elif status_code == 200 and len(posts) == 0:
            status_label = "NOTHING EXISTS FOR THIS TOPIC"
        elif status_code == 200 and len(posts) > 0:
            status_label = "OK"
        else:
            status_label = "FAILED"

        return {
            "host": host,
            "tag": tag,
            "status_code": status_code,
            "status_label": status_label,
            "err_msg": err_msg,
            "posts": posts
        }

    # Run all instance/hashtag requests in parallel (ThreadPoolExecutor, max 8)
    if planned_targets:
        max_w = min(8, len(planned_targets))
        with ThreadPoolExecutor(max_workers=max_w) as executor:
            future_to_tgt = {executor.submit(_query_target, tgt): tgt for tgt in planned_targets}
            for fut in as_completed(future_to_tgt):
                tgt = future_to_tgt[fut]
                try:
                    res = fut.result()
                    host = res["host"]
                    tag = res["tag"]
                    for s in res["posts"]:
                        sid = str(s.get("id"))
                        uid = f"{host}:{sid}"
                        if uid not in statuses_by_uid:
                            statuses_by_uid[uid] = s

                    variant_diagnostics.append({
                        "source": f"mastodon ({host})",
                        "instance": host,
                        "query": f"#{tag}",
                        "variant": f"#{tag}",
                        "status_code": res["status_code"],
                        "status": res["status_label"],
                        "posts_found": len(res["posts"]),
                        "threads_kept": 0,
                        "error": res["err_msg"],
                        "error_text": res["err_msg"]
                    })
                except Exception as ex:
                    host, tag = tgt
                    variant_diagnostics.append({
                        "source": f"mastodon ({host})",
                        "instance": host,
                        "query": f"#{tag}",
                        "variant": f"#{tag}",
                        "status_code": 500,
                        "status": "FAILED",
                        "posts_found": 0,
                        "threads_kept": 0,
                        "error": str(ex),
                        "error_text": str(ex)
                    })

    # Optional /api/v2/search if MASTODON_TOKEN is set
    tok = os.getenv("MASTODON_TOKEN")
    if tok and not tok.startswith("urn:") and time.time() <= mastodon_deadline:
        for host in MASTODON_INSTANCES[:2]:
            try:
                r = requests.get(
                    f"https://{host}/api/v2/search",
                    params={"q": raw, "type": "statuses", "resolve": "false"},
                    headers=hdrs,
                    timeout=min(5.0, max(0.5, mastodon_deadline - time.time()))
                )
                if r.ok:
                    for s in r.json().get("statuses", []):
                        sid = str(s.get("id"))
                        uid = f"{host}:{sid}"
                        if uid not in statuses_by_uid:
                            s["_host"] = host
                            statuses_by_uid[uid] = s
            except Exception:
                pass

    total_statuses = list(statuses_by_uid.values())

    # Adaptive threshold: replies_count >= 3, else >= 1, else >= 0 (single post results not dropped)
    candidates = [s for s in total_statuses if s.get("replies_count", 0) >= 3]
    if len(candidates) < 3:
        candidates = [s for s in total_statuses if s.get("replies_count", 0) >= 1]
    if not candidates and total_statuses:
        candidates = total_statuses[:5]

    candidates.sort(key=lambda s: s.get("replies_count", 0), reverse=True)
    top_candidates = candidates[:5]

    threads = []
    for s in top_candidates:
        if time.time() > mastodon_deadline:
            break
        host = s.get("_host", "mastodon.social")
        sid = str(s.get("id"))
        time_left = max(0.5, mastodon_deadline - time.time())
        try:
            r_ctx = requests.get(f"https://{host}/api/v1/statuses/{sid}/context", headers=hdrs, timeout=min(5.0, time_left))
            ctx = r_ctx.json() if r_ctx.ok else {}
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
                    "platform": "Mastodon",
                    "topic": topic
                })

            if rows:
                root_text = rows[0]["body"]
                first_50 = (root_text[:50].replace("\n", " ") if root_text else f"Mastodon status {sid}").strip()
                replies_count = max(0, len(rows) - 1)
                label_suffix = f"{replies_count} replies" if replies_count > 0 else "no replies yet"

                tier, q_label = classify_thread_match(rows, raw, query_plan)

                threads.append({
                    "topic": topic,
                    "platform": "Mastodon",
                    "rows": rows,
                    "first_50": first_50,
                    "replies_count": replies_count,
                    "quality_tier": tier,
                    "quality_label": q_label,
                    "label": f"{q_label} {first_50} - Mastodon - {label_suffix}",
                    "relevance": s.get("replies_count", 0) + (10 if replies_count > 0 else 1)
                })
        except Exception:
            # Single-post fallback when context call fails
            topic = f"search:masto:{host}:{sid}"
            root_text = strip_html(s.get("content", ""))
            first_50 = (root_text[:50].replace("\n", " ") if root_text else f"Mastodon status {sid}").strip()
            cat = parse_iso_ts(s.get("created_at"))
            acct = s.get("account", {}).get("acct", "unknown")
            author_name = acct if "@" in acct else f"{acct}@{host}"
            single_row = [{
                "id": f"{host}:{sid}",
                "author": author_name,
                "parent_id": None,
                "parent": None,
                "root_id": f"{host}:{sid}",
                "body": root_text,
                "created_utc": cat,
                "ts": cat,
                "subreddit": host,
                "community": host,
                "platform": "Mastodon",
                "topic": topic
            }]
            tier, q_label = classify_thread_match(single_row, raw, query_plan)
            threads.append({
                "topic": topic,
                "platform": "Mastodon",
                "rows": single_row,
                "first_50": first_50,
                "replies_count": 0,
                "quality_tier": tier,
                "quality_label": q_label,
                "label": f"{q_label} {first_50} - Mastodon - no replies yet",
                "relevance": s.get("replies_count", 0) + 1
            })

    searched_rows = [d for d in variant_diagnostics if d.get("status") != "NOT SEARCHED"]
    has_ok_posts = any(d.get("status") == "OK" and d.get("posts_found", 0) > 0 for d in variant_diagnostics)
    all_200_zero = (
        len(searched_rows) > 0 and
        all(d.get("status") == "NOTHING EXISTS FOR THIS TOPIC" for d in searched_rows)
    )
    any_rate_limited = any(d.get("status_code") == 429 for d in variant_diagnostics)

    if has_ok_posts:
        source_status = "ok"
    elif all_200_zero:
        source_status = "nothing exists for this topic"
    elif any_rate_limited:
        source_status = "rate limited"
    elif len(searched_rows) == 0:
        source_status = "nothing exists for this topic"
    else:
        source_status = "failed"

    for diag in variant_diagnostics:
        if diag["posts_found"] > 0:
            diag["threads_kept"] = len(threads)

    diagnostic_summary = {
        "status": source_status,
        "status_code": 200 if has_ok_posts or all_200_zero else 500,
        "posts_found": len(total_statuses),
        "threads_kept": len(threads),
        "message": f"Mastodon: {len(threads)} threads kept from {len(total_statuses)} posts found"
    }

    return threads, diagnostic_summary, variant_diagnostics

# -------------------------------------------------------------
# Cross-Platform Deduplication (R5.6)
# -------------------------------------------------------------
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

    deduped_threads = []
    for th in threads:
        cleaned_rows = []
        for r in th["rows"]:
            if r["id"] in remap_ids or r["id"] in seen_ids:
                continue
            seen_ids.add(r["id"])

            if r.get("parent_id") in remap_ids:
                r["parent_id"] = remap_ids[r["parent_id"]]
                r["parent"] = r["parent_id"]
            if r.get("root_id") in remap_ids:
                r["root_id"] = remap_ids[r["root_id"]]

            cleaned_rows.append(r)

        if cleaned_rows:
            th["rows"] = cleaned_rows
            th["replies_count"] = max(0, len(cleaned_rows) - 1)
            label_suffix = f"{th['replies_count']} replies" if th['replies_count'] > 0 else "no replies yet"
            q_label = th.get("quality_label", "")
            th["label"] = f"{q_label} {th['first_50']} - {th['platform']} - {label_suffix}".strip()
            deduped_threads.append(th)

    return deduped_threads

# -------------------------------------------------------------
# Main Live Search Coordinator (R1, R2, R5)
# -------------------------------------------------------------
def search_live_keywords(raw_input: str):
    """
    Coordinates keyword search across Reddit (2019 dataset), Bluesky and Mastodon.
    - Runs within a 30-second overall timeout.
    - Caches results for 10 minutes (600s).
    - Returns structured diagnostics for 'How we searched'.
    - Handles soft-gibberish and hard-invalid gracefully.
    """
    val = sv.validate_and_plan(raw_input)
    if not val["valid"]:
        return {
            "type": "error",
            "valid": False,
            "reason": val["reason"],
            "query": raw_input,
            "message": val["reason"],
            "counts": {"reddit": 0, "bluesky": 0, "mastodon": 0, "total": 0},
            "threads": [],
            "diagnostics": {"status": "hard-invalid", "message": val["reason"]}
        }

    clean_k = val["normalised"].lower()
    now = time.time()

    if clean_k in SEARCH_CACHE:
        entry = SEARCH_CACHE[clean_k]
        if now - entry["timestamp"] < 600:
            return entry["result"]

    deadline = now + 30.0
    PROGRESS_STATUS[clean_k] = {
        "text": f"Analyzing query: '{val['searching_for']}'...",
        "done": False,
        "error": "",
        "result": None
    }

    import store
    # Clear previous search results so current query's results are not accumulated (R2.2)
    store.clear_search_results()

    plan = qp.plan_query(val["searching_for"])

    # 1. Search Reddit CSV (R2.1)
    PROGRESS_STATUS[clean_k]["text"] = "Searching Reddit (2019 r/politics archive)..."
    reddit_threads = store.search_reddit(val["searching_for"], plan)
    reddit_count = len(reddit_threads)
    reddit_posts = sum(t.get("posts_count", len(t.get("rows", []))) for t in reddit_threads)

    reddit_diag = {
        "status": "ok" if reddit_count > 0 else "nothing exists for this topic",
        "status_code": 200,
        "posts_found": reddit_posts,
        "threads_kept": reddit_count,
        "message": f"Reddit (2019): {reddit_count} threads ({reddit_posts} comments)" if reddit_count > 0 else "Reddit (2019): No matching comments"
    }

    # 2. Search Bluesky (R5.2) and Mastodon (R5.3) concurrently in parallel so neither starves the other
    PROGRESS_STATUS[clean_k]["text"] = "Searching Bluesky & Mastodon in parallel..."
    with ThreadPoolExecutor(max_workers=2) as executor:
        fut_bsky = executor.submit(search_bluesky, plan, deadline)
        fut_masto = executor.submit(search_mastodon, plan, deadline)
        bsky_threads, bsky_diag, bsky_variants = fut_bsky.result()
        masto_threads, masto_diag, masto_variants = fut_masto.result()

    PROGRESS_STATUS[clean_k]["text"] = "Deduplicating and ranking results..."

    all_threads = reddit_threads + bsky_threads + masto_threads

    # Sort by match quality first, then replies.
    # Never count more than 3 weak matches.
    strong_threads = [t for t in all_threads if t.get("quality_tier", 4) < 4]
    weak_threads = [t for t in all_threads if t.get("quality_tier", 4) == 4]
    weak_threads.sort(key=lambda t: -t.get("replies_count", 0))
    weak_threads = weak_threads[:3]

    final_threads = deduplicate_threads(strong_threads + weak_threads)
    final_threads.sort(key=lambda t: (t.get("quality_tier", 4), -t.get("replies_count", 0)))

    # Save to store for analysis dropdown
    for th in final_threads:
        store.save_search_thread(th["topic"], th["rows"], {
            "platform": th["platform"],
            "label": th["label"],
            "quality_tier": th.get("quality_tier", 4),
            "quality_label": th.get("quality_label", "[Weak: 1 keyword]"),
            "first_50": th["first_50"],
            "replies": th["replies_count"],
            "query": val["searching_for"],
            "relevance": th.get("relevance", 0)
        })

    count_reddit = sum(1 for t in final_threads if "reddit" in t["platform"].lower())
    count_bsky = sum(1 for t in final_threads if "bluesky" in t["platform"].lower())
    count_masto = sum(1 for t in final_threads if "mastodon" in t["platform"].lower())
    total_found = len(final_threads)

    biggest_topic = final_threads[0]["topic"] if final_threads else None

    # Handle summary message and soft-gibberish (R1.3)
    if total_found > 0:
        parts = []
        if count_reddit > 0: parts.append(f"Reddit {count_reddit}")
        if count_bsky > 0: parts.append(f"Bluesky {count_bsky}")
        if count_masto > 0: parts.append(f"Mastodon {count_masto}")
        summary_msg = f"Found {total_found} threads ({', '.join(parts)})"
    else:
        if val["is_soft_gibberish"]:
            summary_msg = sv.SOFT_GIBBERISH_TEMPLATE.format(query=val['normalised'])
        else:
            summary_msg = (
                f"No public posts found across Reddit (2019 archive), Bluesky, or Mastodon for '{val['normalised']}'. "
                "Try broader keywords or synonyms."
            )

    all_variants_diag = (
        [{"source": "reddit", "variant": val["searching_for"], "status_code": 200, "posts_found": reddit_posts, "threads_kept": reddit_count, "error": None}]
        + bsky_variants
        + masto_variants
    )

    diagnostics = {
        "query": val["normalised"],
        "searching_for": val["searching_for"],
        "is_long_caption": val["is_long_caption"],
        "extracted_keywords": val["extracted_keywords"],
        "is_soft_gibberish": val["is_soft_gibberish"],
        "sources": {
            "reddit": reddit_diag,
            "bluesky": bsky_diag,
            "mastodon": masto_diag
        },
        "variants": all_variants_diag
    }

    result = {
        "type": "search",
        "valid": True,
        "query": raw_input,
        "searching_for": val["searching_for"],
        "is_long_caption": val["is_long_caption"],
        "is_soft_gibberish": val["is_soft_gibberish"],
        "message": summary_msg,
        "counts": {
            "reddit": count_reddit,
            "bluesky": count_bsky,
            "mastodon": count_masto,
            "total": total_found
        },
        "biggest_topic": biggest_topic,
        "diagnostics": diagnostics,
        "threads": [
            {
                "topic": t["topic"],
                "platform": t["platform"],
                "comments": len(t["rows"]),
                "posts": len(t["rows"]),
                "label": t["label"],
                "quality_tier": t.get("quality_tier", 4),
                "quality_label": t.get("quality_label", ""),
                "relevance": t.get("relevance", 0)
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
    Main dispatch endpoint for /fetch:
    1. Validates input (R1.1, R1.2).
    2. If link, fetches Bluesky or Mastodon post link.
    3. If keywords, runs multi-source reliable search (R1, R2, R5).
    """
    val = sv.validate_and_plan(input_text)
    if not val["valid"]:
        # Hard-invalid
        return {
            "type": "error",
            "valid": False,
            "reason": val["reason"]
        }

    t = val["normalised"]
    if val["is_url"]:
        topic, rows = fetch_link(t)
        return {
            "type": "link",
            "valid": True,
            "topic": topic,
            "posts": len(rows),
            "rows": rows
        }
    else:
        return search_live_keywords(input_text)
