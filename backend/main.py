import time
import json
import logging
from datetime import datetime, timezone
import networkx as nx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel
import analytics as A, store, live, detector, news as nw, neo4j_loader
try:
    import search_validator as sv
except ImportError:
    sv = None
import traceback

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nodezero")

app = FastAPI(title="NodeZero")

# CORS and GZip middleware:
# GZipMiddleware compresses payloads > 1000 bytes.
# CORSMiddleware wraps outermost so all responses (including errors) have CORS headers.
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "*",
    "Access-Control-Allow-Headers": "*",
}

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "HTTPException", "detail": exc.detail},
        headers=CORS_HEADERS,
    )

@app.exception_handler(StarletteHTTPException)
async def starlette_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "HTTPException", "detail": exc.detail},
        headers=CORS_HEADERS,
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "ValidationError", "detail": str(exc.errors())},
        headers=CORS_HEADERS,
    )

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    tb = traceback.format_exc()
    logger.error("Unhandled Exception at %s:\n%s", request.url, tb)
    return JSONResponse(
        status_code=500,
        content={"error": type(exc).__name__, "detail": str(exc), "traceback": tb},
        headers=CORS_HEADERS,
    )

@app.get("/")
def root():
    return {"status": "ok", "service": "NodeZero API"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/test/error500")
def test_error_500():
    raise RuntimeError("Simulated internal server error")

def ctx(topic: str):
    clean_topic = topic.replace("t3_", "")
    if (not store.has_reddit_csv() and
        clean_topic not in store.LIVE_POSTS and
        clean_topic not in store.SEARCH_POSTS):
        raise HTTPException(404, "Run fetch_reddit.py first")

    # Step 1: Load CSV / posts
    t0 = time.perf_counter()
    posts = store.load(topic)
    if not posts:
        raise HTTPException(404, f"unknown topic {topic}")
    t_load = time.perf_counter() - t0

    # Step 2: Build graph
    t0 = time.perf_counter()
    byid, pg, ug, root_placeholder, root_post = A.build(posts, topic)
    t_build = time.perf_counter() - t0

    # Step 3: Communities (nx.community.louvain_communities seed=42)
    t0 = time.perf_counter()
    account_comm = A.assign_communities(posts, ug)
    t_comm = time.perf_counter() - t0

    # Step 4: PageRank
    t0 = time.perf_counter()
    if hasattr(A, "safe_pagerank"):
        pr = A.safe_pagerank(ug, weight="weight")
    else:
        pr = nx.pagerank(ug, weight="weight") if ug.number_of_nodes() else {}
    t_pr = time.perf_counter() - t0

    # Step 5: Bot scores and heuristics
    t0 = time.perf_counter()
    usr = A.users(posts, ug, account_comm, pr=pr)
    t_bot = time.perf_counter() - t0

    root = A.find_root(posts, topic, byid, root_post, usr)

    timings = {
        "load_csv": t_load,
        "build_graph": t_build,
        "communities": t_comm,
        "pagerank": t_pr,
        "bot_scores": t_bot,
    }
    return posts, byid, pg, ug, usr, root, timings

@app.get("/topics")
def topics():
    return store.topics()

@app.get("/accounts/search")
def accounts_search(q: str = "", limit: int = 10):
    return store.search_accounts(q, limit=limit)

@app.get("/analysis/{topic}")
def analysis(topic: str, limit: int = 300, include_user: str = None):
    # Only use cache if no custom include_user requested
    if not include_user:
        cached = store.get_cached_analysis(topic, limit)
        if cached:
            return cached

    posts, byid, pg, ug, usr, root, timings = ctx(topic)

    if limit and limit > 0:
        top = sorted(usr.values(), key=lambda u: -u["influence"])[:limit]
        keep = {u["name"] for u in top} | ({root["author"]} & usr.keys() if root else set())
        if include_user and include_user in usr:
            keep.add(include_user)
            if ug.has_node(include_user):
                keep.update(ug.predecessors(include_user))
                keep.update(ug.successors(include_user))
    else:
        keep = set(usr.keys())

    nodes = [
        dict(
            id=u["name"],
            community=u["community"],
            influence=u["influence"],
            bot_score=u["bot_score"],
            flagged=bool(u["flagged"]),
            declared_automation=bool(u.get("declared_automation", False)),
            declared_label=u.get("declared_label", ""),
            misinfo_score=u.get("misinfo_score", 0.0),
            misinfo_signals=u.get("misinfo_signals", []),
            posts=u["posts"],
            is_root=bool(root and u["name"] == root["author"])
        )
        for u in usr.values() if u["name"] in keep
    ]
    links = [
        dict(
            source=b,
            target=a,
            weight=d["weight"],
            replies=d["weight"],
            first_ts=d.get("first_ts", 0)
        )
        for a, b, d in ug.edges(data=True)
        if a in keep and b in keep
    ]

    res = dict(topic=topic, summary=A.summary(posts, usr, root), nodes=nodes, links=links)

    # Step 6: JSON size & serialization time
    t0 = time.perf_counter()
    json_bytes = len(json.dumps(res).encode("utf-8"))
    t_json = time.perf_counter() - t0
    timings["json_size"] = t_json

    # Identify and print the 3 slowest steps
    sorted_steps = sorted(timings.items(), key=lambda x: -x[1])[:3]
    slowest_msg = ", ".join(f"{name} ({dur*1000:.1f}ms)" for name, dur in sorted_steps)
    step_details = ", ".join(f"{k}={v*1000:.1f}ms" for k, v in timings.items())
    log_msg = f"[TIMING thread={topic}] Steps: {step_details} | JSON: {json_bytes/1024:.1f} KB | Top 3 slowest: {slowest_msg}"
    print(log_msg)
    logger.info(log_msg)

    if not include_user:
        store.set_cached_analysis(topic, limit, res)

    return res

@app.get("/user/{topic}/{name}")
def user(topic: str, name: str):
    posts, byid, pg, ug, usr, root, _ = ctx(topic)
    if name not in usr:
        raise HTTPException(404, "unknown user")
    clean_topic = topic.replace("t3_", "")
    clean_id = lambda s: s.split("_", 1)[1] if isinstance(s, str) and (s.startswith("t1_") or s.startswith("t3_")) else str(s)
    mine = sorted(
        (p for p in posts if (f"[deleted:{clean_id(p['id'])}]" if p.get("author") in ("[deleted]", "[removed]") else p.get("author")) == name),
        key=lambda p: p.get("created_utc", 0)
    )
    path = A.trace(mine[-1]["id"], byid) if mine else []

    # First share date & time
    first_ts = usr[name].get("first")
    first_share_utc = (
        datetime.fromtimestamp(first_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if first_ts else "Not available"
    )

    # Last 5 posts with links
    last_5_posts = []
    for p in reversed(mine[-5:]):
        p_ts = p.get("created_utc", 0)
        p_utc = (
            datetime.fromtimestamp(p_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            if p_ts else "Not available"
        )
        plat = (p.get("platform") or usr[name].get("platform") or "").lower()
        cid = clean_id(p.get("id", ""))

        if "reddit" in plat:
            post_link = f"https://www.reddit.com/r/politics/comments/{clean_topic}/_/{cid}"
        elif "bluesky" in plat:
            uri = p.get("id") or p.get("root_id") or ""
            rkey = uri.split("/")[-1]
            h = p.get("author") or "unknown"
            post_link = f"https://bsky.app/profile/{h}/post/{rkey}"
        elif "mastodon" in plat:
            host = p.get("subreddit") or "mastodon.social"
            sid = str(p.get("id", "")).split(":")[-1]
            auth = str(p.get("author") or "").split("@")[0]
            post_link = f"https://{host}/@{auth}/{sid}"
        else:
            post_link = f"https://www.reddit.com/r/politics/comments/{clean_topic}/_/{cid}"

        last_5_posts.append({
            "id": cid,
            "body": p.get("body", "")[:280],
            "created_utc": p_ts,
            "date_utc": p_utc,
            "link": post_link
        })

    # Location (R3.5): show ONLY if account's public profile states one.
    # Label 'self-reported, unverified'. Reddit comments have no location field -> 'Not available'.
    # NEVER guess location from writing style, IP or timestamps.
    plat = (usr[name].get("platform") or "").lower()
    if "reddit" in plat or not plat:
        location_data = {"text": "Not available", "status": "Not available"}
    else:
        # Check if live post carried profile location
        loc_val = None
        for p in mine:
            if p.get("location"):
                loc_val = p.get("location")
                break
        if loc_val:
            location_data = {"text": str(loc_val), "status": "self-reported, unverified"}
        else:
            location_data = {"text": "Not available", "status": "Not available"}

    return dict(
        usr[name],
        first_share_utc=first_share_utc,
        first_share_ts=first_ts,
        last_5_posts=last_5_posts,
        location=location_data,
        samples=[p["body"][:160] for p in mine[-3:]],
        trace=[dict(author=p.get("author") or "[deleted]", community=p.get("subreddit") or "unknown") for p in path]
    )

@app.get("/news")
def news(q: str = "", when: str = "7d", after: str = "", before: str = ""):
    return nw.get_news(q, when, after, before)

@app.get("/classify")
def classify(text: str):
    return detector.score_text_misinformation(text)

class Req(BaseModel):
    url: str = ""
    query: str = ""

@app.post("/fetch")
def fetch(r: Req):
    target_text = (r.url or r.query or "").strip()
    if not target_text:
        raise HTTPException(400, "URL or query keywords required")

    if sv is not None:
        val = sv.validate_and_plan(target_text)
        if not val.get("valid", True):
            return JSONResponse(
                status_code=422,
                content={"valid": False, "reason": val.get("reason", "Invalid search input")},
                headers=CORS_HEADERS,
            )

    try:
        res = live.fetch(target_text)
    except Exception as e:
        raise HTTPException(400, f"Could not fetch: {e}")

    if isinstance(res, dict) and (res.get("type") == "error" or not res.get("valid", True)):
        hard_msg = getattr(sv, "HARD_INVALID_MESSAGE", "Invalid search input.") if sv else "Invalid search input."
        return JSONResponse(
            status_code=422,
            content={"valid": False, "reason": res.get("reason", hard_msg)},
            headers=CORS_HEADERS,
        )

    if isinstance(res, dict) and res.get("type") == "link":
        store.save_live(res["topic"], res["rows"])
        return dict(type="link", topic=res["topic"], posts=res["posts"])

    return res

@app.post("/search/clear")
def search_clear():
    store.clear_search_results()
    return {"cleared": True}

@app.get("/fetch/status")
def fetch_status(q: str = ""):
    clean_q = (q or "").strip().lower()
    return live.PROGRESS_STATUS.get(clean_q, {"text": "", "done": True, "error": "", "result": None})

@app.get("/neo4j/status")
def neo4j_status():
    return neo4j_loader.get_status()

@app.get("/neo4j/sync/status")
def neo4j_sync_status():
    return neo4j_loader.get_sync_status()

@app.get("/neo4j/check")
def neo4j_check():
    return neo4j_loader.check_database_vs_dashboard()

@app.get("/neo4j/top-replied")
def neo4j_top_replied(limit: int = 10):
    return neo4j_loader.get_top_replied_to(limit)

@app.post("/neo4j/cleanup")
def neo4j_cleanup():
    return neo4j_loader.cleanup_database()

@app.post("/neo4j/sync")
def neo4j_sync():
    if not store.has_reddit_csv():
        raise HTTPException(400, "No Reddit data loaded. Run fetch_reddit.py first")

    status = neo4j_loader.get_status()
    if not status.get("connected"):
        return {
            "success": False,
            "message": f"Neo4j is not connected: {status.get('message', 'unknown error')}"
        }

    topics_list = store.topics().get("reddit", [])
    if not topics_list:
        return {"success": False, "message": "No Reddit topics found in reddit.csv"}

    nodes_dict = {}
    edges_list = []

    for t in topics_list:
        clean_t = t["topic"].replace("t3_", "")
        try:
            posts, byid, pg, ug, usr, root, _ = ctx(clean_t)
            for u in usr.values():
                nodes_dict[u["name"]] = {
                    "name": u["name"],
                    "bot_score": float(u["bot_score"]),
                    "influence": float(u["influence"]),
                    "community": str(u["community"]),
                }
            for a, b, d in ug.edges(data=True):
                edges_list.append({
                    "src": str(a),
                    "dst": str(b),
                    "thread": clean_t,
                    "count": int(d.get("weight", 1)),
                })
        except Exception:
            continue

    return neo4j_loader.trigger_background_sync(list(nodes_dict.values()), edges_list)

@app.on_event("shutdown")
def shutdown_event():
    neo4j_loader.close()
