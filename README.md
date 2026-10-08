# NodeZero

Traces how a post spreads through reply networks. Reddit (Kaggle/Pushshift) is the main dataset;
Bluesky and Mastodon are fetched live, only when you paste a link.

Stack: Python, Neo4j, NetworkX, FastAPI, React + D3.

## Run it (PowerShell)

1. Start your Neo4j Desktop database (neo4j://127.0.0.1:7687).
2. Backend:
```
cd backend
python -m venv venv; .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env        # then put your Neo4j password in .env
python make_demo_data.py      # optional: synthetic DEMO threads, not real data
python ingest_reddit.py --csv ../data/demo_threads.csv
uvicorn main:app --reload     # http://127.0.0.1:8000/docs
```
3. Frontend (second terminal):
```
cd frontend
npm install
npm run dev                   # http://localhost:5173
```

## Loading real Reddit data
```
python ingest_reddit.py --csv ../data/thread_sample.csv
python ingest_reddit.py --kaggle --roots t3_b7sa1t,t3_b7tup6 --max-lines 5000000
```
CSV columns: `id, author, parent_id, root_id, body, created_utc, subreddit`.
Each `root_id` becomes one topic. The `subreddit` column drives the community colours,
so keep it in your CSV. If the Kaggle files are submissions only (no comments), there are no
reply edges, so you need a comments dump for the graph to have structure.

## About the Data
The Reddit dataset in NodeZero is a historical archive originating from Pushshift / Kaggle dumps of `r/politics` on 1 April 2019, comprising 5 discussion threads and 7,826 comments. In contrast, Bluesky (AT Protocol) and Mastodon (ActivityPub) datasets are fetched live from their respective production APIs in real time when you search or provide a URL. None of the fields across any dataset are simulated: all timestamps (UTC), author handles, reply trees, comment bodies, and engagement metrics reflect genuine platform records.

## Live data
Paste a post link into "Live fetch" (or `POST /fetch {"url": ...}`):
- Bluesky: `https://bsky.app/profile/<handle>/post/<id>` (community = handle domain)
- Mastodon: `https://<instance>/@user/<status id>` (community = account's instance)
Some Mastodon instances require login to read replies; those will return an error.

## Where each feature lives
| Feature | Code |
|---|---|
| Plain-language spread summary | `analytics.summary` shown by `Summary` in `App.jsx` |
| Two topics side by side | "Compare two topics" checkbox, `App.jsx` |
| Community colouring | `community` per node, `Graph.jsx` legend and fill via `louvain_communities(seed=42)` |
| Root detection with confidence | `analytics.find_root` (score, label, reasons, candidates) |
| Global account search | `store.search_accounts` across ALL loaded data, `/accounts/search`, `App.jsx` |
| Live fetch & keyword search | `live.fetch`, `search_live_keywords` across Bluesky & Mastodon, `/fetch`, `/fetch/status` |
| Background Neo4j sync | `neo4j_loader.trigger_background_sync` in 5,000 UNWIND batches |

## How Bot Detection Works

NodeZero evaluates account automation using a transparent three-rule heuristic scoring system (range 0–100 with a threshold of 60 for flagging):

- **Rule 1: Reply Volume (+40 pts)**: Triggered when an account's reply count in the thread exceeds the 95th percentile baseline of all participants.
  - *Why chosen*: Automated accounts and bot scripts frequently post at superhuman volumes to flood or dominate conversation cascades.
- **Rule 2: Digit-Heavy Username (+30 pts)**: Triggered when numeric digits comprise >20% of the account username (exempting decentralized identifiers like DIDs).
  - *Why chosen*: Bulk-registered automated bot accounts routinely use auto-generated numerical suffixes.
- **Rule 3: Out/In Reply Topology (+30 pts)**: Triggered when an account has an out/in reply ratio > 3.0 with at least 2 outgoing replies.
  - *Why chosen*: Broadcast and amplifier bots reply widely to many users without generating reciprocal, two-way conversational engagement.

### Heuristic Classification Label
Accounts scoring ≥ 60 are designated with the label **"automation-like activity (heuristic)"**. Detections are explicitly probabilistic heuristics and are never described as "proven" bots.

### Honest Limitations
This heuristic approach cannot detect:
1. **Modern LLM Bots**: AI-driven accounts utilizing large language models that mimic natural conversational cadences and post at human-like frequencies.
2. **Low-Activity Sleepers**: Dormant or sleeper accounts that post infrequently to evade volume-based statistical detection.
3. **Single-Comment Astroturfers**: Coordinated brigading campaigns where individual participating accounts each post only once or twice, evading aggregate per-user thresholds.

## Scoring & Heuristics Notes

### Misinformation Risk Score (Credibility Heuristic)
Misinformation Risk is a quantitative linguistic credibility score evaluated across an account's recent comments:
- **Base Score**: 0.15 (15%) baseline credibility.
- **Sensationalist / Alarmist Phrasing (+0.25)**: Matches conspiracy/alarmist keywords (e.g., `breaking`, `exposed`, `proof`, `coverup`, `wake up`, `sheeple`, `secret agenda`, `100% confirmed`, `hoax`, `false flag`).
- **Excessive Shouting Intensity (+0.20)**: Evaluates uppercase character ratio when text contains at least 15 alphabetical letters. Triggered if uppercase ratio > 45%.
- **Excessive Punctuation (+0.15)**: Triggered by multiple consecutive exclamation or question marks (`???`, `!!!`, `!?!`).
- **Score Bounds & Classification**:
  - Clamped between 0.05 (5%) and 0.95 (95%).
  - **High-Risk Misinformation**: Score ≥ 0.50 (50%).
  - **Questionable**: Score between 0.40 and 0.49.
  - **Likely Reliable**: Score < 0.40.
  - An account's score represents the mean average across their sampled comments (defaulting to 0.15 if no text content).

## Rules & Badges Reference

### 1. High-Risk Accounts
An account is classified as **High-Risk** if either of the following conditions is met:
- **Misinformation Risk Score ≥ 0.50 (50%)**: Average linguistic credibility score across recent comments evaluates to 0.50 or higher based on sensationalist/alarmist phrasing (+0.25), excessive uppercase shouting ratio > 45% (+0.20), and excessive exclamation/question punctuation (+0.15).
- **Flagged Bot-Likely (Bot Score ≥ 60/100)**: Account behavioral heuristic score reaches 60 or more.

### 2. Isolate High-Risk Misinformation Clusters
When this filter is enabled on the cascade graph:
- It isolates the subnetwork consisting of **seed high-risk accounts** (accounts flagged as bot-likely with bot score ≥ 60 or questionable text with misinformation risk score ≥ 0.40).
- It retains their **direct 1-hop reply neighbors** (any account that replied directly to or received replies from a seed account).
- All disconnected or unrelated organic sub-threads are filtered out to highlight the propagation vector.

### 3. Global Account Search
- Queries **ALL accounts across all loaded datasets** (Reddit, Live, and Search results), not just the open thread's top 300.
- Case-insensitive partial matching showing up to 10 ranked suggestions with their primary thread and comment counts.
- Selecting a match switches to the thread where the account has the most comments, guarantees the account and its direct 1-hop neighbors are included in the view (even if outside the top 300), smoothly zooms and centers onto the account node with a glowing pulse, and displays its detailed profile panel.
- If no match is found, reports the exact sources and threads that were searched.

### 4. Performance & Caching (Target: Thread switch < 2s, Search < 0.5s)
- **Step Timing**: Each thread analysis measures execution time for `load_csv`, `build_graph`, `communities`, `pagerank`, `bot_scores`, and `json_size`, printing the 3 slowest steps to the console and logs.
- **Dual-Tier Cache**: Precomputed thread graph results are cached in-memory and on-disk (`data/.cache/*.pkl`), keyed to the CSV modification timestamp (`mtime`), enabling sub-5ms thread switching.
- **Louvain Communities**: Uses `nx.community.louvain_communities(seed=42)` for rapid modularity clustering while preserving the "at least 5 accounts" rule.
- **Instant Graph Rendering**: Force simulation executes a fixed ~300 ticks in-memory without intermediate DOM painting, drawing the graph in a single batch pass with zero per-tick re-renders.
- **Top 300 Nodes & GZip**: Default payloads send only top 300 accounts by influence, with GZip compression enabled on FastAPI.
- **Neo4j Background Sync**: Runs in background threads using idempotent `UNWIND` batches of 5,000, streaming live progress text, and skipping execution if dataset contents are unchanged.

### 5. Live Fetch & Keyword Search (Bluesky & Mastodon)
- **Link Fetch**:
  - Bluesky: `https://bsky.app/profile/<handle>/post/<rkey>` resolves handle and flattens thread up to 2,000 replies. Automatically falls back to authenticated session using `BSKY_HANDLE` and `BSKY_APP_PASSWORD` on 401/403.
  - Mastodon: `https://<server>/@user/<id>` queries server status and context endpoints, stripping HTML content.
  - Preserves root post in dataset so author confidence is rated as `confident`.
- **Keyword Search**:
  - Live fetch input accepts plain search terms (e.g., `vpn`, `nepal flood`, `vaccine`).
  - Bluesky: Searches top 25 posts via Bearer token, filters threads with `replyCount >= 5` (top 5 posts), and flattens descendants. Retries with 3, 2, 1 longest words if no matches are found.
  - Mastodon: Searches hashtag timelines for alphanumeric keywords, plus `/api/v2/search` if `MASTODON_TOKEN` is configured, keeping statuses with `replies_count >= 3`.
  - Deduplication: Removes duplicate IDs and detects identical long text (40+ characters) across platforms, keeping a single canonical post and re-pointing all replies and thread root pointers to it.
  - Dropdown Grouping: Discovered threads are added under a `"Search results"` group formatted as `"<first 50 chars> - bluesky/mastodon - N replies"`. The largest thread opens automatically with result counts: `"Found X threads (Bluesky A, Mastodon B)"`.
  - Background Execution: Operates under a 30-second deadline with live progress reporting and caches queries for 10 minutes. Missing credentials emit friendly instructions without crashing.

### 6. Media Trends
Correlates viral discussion cascades with mainstream journalism:
- Queries Google News RSS search (`https://news.google.com/rss/search`) using the thread topic keywords or custom search queries.
- Filters by time range: past 24 hours (`1d`), past 7 days (`7d`), past 1 year (`1y`), or exact start/end dates (`after:YYYY-MM-DD before:YYYY-MM-DD`).
- Parses, normalizes, and deduplicates up to 30 top headlines with publisher source, publication timestamp, and external article URLs.

## Neo4j Graph Model & Database Cleanup

### Schema Architecture
NodeZero models **account-level conversation cascades**:
- **Nodes**: `(:User {name, bot_score, influence, community})`.
- **Relationships**: `(:User)-[:REPLIED_TO {thread, count}]->(:User)`.
- **Index**: `CREATE INDEX user_name_index IF NOT EXISTS FOR (u:User) ON (u.name)`.
- **Sync**: Batch loaded in chunks of 1,000 using idempotent `MERGE`.

### Explanation of Post Nodes and POSTED Relationships
An earlier revision of the data loader generated separate `(:Post)` nodes and `(:User)-[:POSTED]->(:Post)` relationships. This bipartite representation created data redundancy and inflated database sizes (5,224 nodes and 10,760 relationships vs 4,702 users and 6,800 edges in the dashboard). Because NodeZero focuses strictly on account-to-account reply influence and diffusion dynamics, `(:Post)` nodes and `[:POSTED]` relationships are deprecated and removed.

To clean up legacy entities and normalize relationships in Neo4j:
```cypher
// Remove duplicate property names
MATCH (u:User) WHERE u.bot_likelihood_score IS NOT NULL
SET u.bot_score = coalesce(u.bot_score, u.bot_likelihood_score)
REMOVE u.bot_likelihood_score;

MATCH ()-[r:REPLIED_TO]->() WHERE r.reply_count IS NOT NULL
SET r.count = coalesce(r.count, r.reply_count)
REMOVE r.reply_count;

// Normalize duplicate relationship types (REPLY_TO -> REPLIED_TO)
MATCH (a:User)-[r:REPLY_TO]->(b:User)
MERGE (a)-[r2:REPLIED_TO {thread: coalesce(r.thread, '')}]->(b)
ON CREATE SET r2.count = coalesce(r.count, r.reply_count, 1)
ON MATCH SET r2.count = r2.count + coalesce(r.count, r.reply_count, 1)
DELETE r;

// Remove deprecated Post nodes and POSTED edges
MATCH (p:Post) DETACH DELETE p;
MATCH ()-[r:POSTED]->() DELETE r;
```

### Top Replied-To Accounts Query
When querying the most replied-to accounts, exclude thread root placeholders:
```cypher
MATCH ()-[r:REPLIED_TO]->(u:User)
WHERE NOT u.name STARTS WITH '[root:'
RETURN u.name AS user, sum(r.count) AS reply_count
ORDER BY reply_count DESC
LIMIT 10;
```

### Database Consistency Check
NodeZero provides a verification endpoint `GET /neo4j/check` that compares in-memory Reddit dataset counts (4,702 unique users, 6,800 reply edges) against the connected Neo4j database counts and returns any discrepancies.

