# NodeZero – Tracing the Invisible Threads Behind Viral Misinformation

Reply networks modelled as directed graphs. Pick a thread -> see its cascade (root at the top, arrows = how it spread),
influence ranking, bot-likelihood heuristic, root tracing, timeline replay. News headlines for a topic sit in the sidebar.
Backend: FastAPI + NetworkX (Neo4j optional). Frontend: React + D3.

## Run
```bash
cd backend && pip install -r requirements.txt && uvicorn main:app --reload      # http://localhost:8000
cd frontend && npm install && npm run dev                                       # http://localhost:5173
```
Set secrets in the SAME terminal before `uvicorn` (PowerShell `$env:NAME="value"`):
`BSKY_HANDLE`, `BSKY_APP_PASSWORD` (Bluesky search), `MASTODON_TOKEN` (optional `MASTODON_INSTANCE`), `YOUTUBE_API_KEY` (YouTube comments, free key from Google Cloud Console > YouTube Data API v3), `KAGGLE_API_TOKEN` (fetch_reddit only).

## Data (run from backend/; CSV columns id,author,parent_id,root_id,body,created_utc)
- `python fetch_reddit.py 20000` or `python fetch_reddit.py 50000 mine` (the 5 project threads) -> data/reddit.csv. [deleted]/[removed] rows are kept so parent chains don't break.
- `python fetch_bluesky.py 5000` (Jetstream firehose, auto-reconnect) -> data/bluesky.csv
- `python fetch_mastodon.py` (trending) or `python fetch_mastodon.py tag1,tag2` -> data/mastodon.csv
- YouTube: paste a video link in the search bar, or `python fetch_youtube.py <link>` -> data/youtube.csv (replies are one level deep).
- Any other platform (e.g. Instagram comments you collected yourself): save a CSV with the same 6 columns as data/<name>.csv and it appears in the source dropdown. Instagram/Facebook/TikTok/X have no open API for this, so nothing is fetched automatically.
- Top bar search: finds posts on a topic across Bluesky + Mastodon + the Reddit file, removes duplicates -> data/search.csv, then opens the busiest thread.

## How the results are computed
- Influence: PageRank on the reply graph (replier -> replied-to), scaled 0-1.
- Bot likelihood (0-100, heuristic only): reply volume above the 95th percentile +40, username digit ratio above 20% +30, out/in-degree ratio above 3 +30. Flagged at 60+. Bluesky DIDs skip the digit rule.
- Root tracing: hop-by-hop walk, max 50 steps, stops on a cycle.
- Experimental AI detector (off by default): small TF-IDF + logistic regression on data/labeled.csv (demo set). Not used by any other result. Replace labeled.csv (text,label; 1 = misinformation), run `python detector.py`.

## Optional Neo4j
`pip install neo4j`, set NEO4J_PASSWORD, `python neo4j_export.py data/reddit.csv`

## API
`POST /load` · `/threads` · `/cascade?root=` · `/network-graph` · `/summary?root=` · `/top-influencers` · `/top-bot-scores` · `/user/{name}` · `/root/{thread}` · `/trace-root/{author}` · `POST /search-live?q=` · `/news?q=` · `POST /score-all` · `/classify?text=`
