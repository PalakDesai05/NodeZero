import { useEffect, useState, useRef } from "react";
import Graph from "./Graph.jsx";
import * as api from "./api.js";

const when = (t) => (t ? new Date(t * 1000).toLocaleString() : "unknown");

function Summary({ s, onRoot, onFilterFlagged }) {
  const r = s?.root;
  return (
    <div className="summary">
      <p className="headline">{s?.headline}</p>
      <div className="summary-badges">
        <span className="badge info">Posts: {s?.posts}</span>
        <span className="badge info">Accounts: {s?.accounts}</span>
        <span className="badge info">Communities: {s?.communities}</span>
        {s?.flagged > 0 ? (
          <button
            className="badge badge-btn danger"
            onClick={onFilterFlagged}
            title="Click to highlight flagged accounts"
          >
            ⚠️ {s.flagged} Flagged Bot-Likely
          </button>
        ) : (
          <span className="badge success">✓ 0 Flagged Bots</span>
        )}
        {s?.high_risk > 0 && (
          <span
            className="badge warning"
            title="High-Risk Accounts: Accounts with misinformation risk score ≥ 50% (sensationalism [+0.25], excessive shouting caps [+0.20], extreme punctuation [+0.15]) or flagged as bot-likely (bot heuristic score ≥ 60 based on reply volume above 95th percentile [+40], handle digit ratio > 20% [+30], and out/in reply ratio > 3.0 [+30])."
          >
            🛡️ {s.high_risk} High-Risk Accounts
          </span>
        )}
      </div>

      {r && (
        <p className={`root ${r.label}`}>
          <strong>{r.post_type || r.type || "Earliest comment"}:</strong>{" "}
          <button onClick={() => onRoot(r.author)}>{r.author}</button> —{" "}
          <span className="root-score">{r.label}</span>. {r.reason}.
          {r.label === "ambiguous" && r.candidates && r.candidates.length > 1 && (
            <span className="other-cands">
              {" "}
              (Other candidate roots:{" "}
              {r.candidates
                .slice(1)
                .map((c) => c.author)
                .join(", ")}
              )
            </span>
          )}
        </p>
      )}
    </div>
  );
}

function NewsPanel({ topic, onClose }) {
  const [q, setQ] = useState(
    topic.replace("t3_", "").replace("bsky:", "").replace("masto:", "").replace("search:bsky:", "").replace("search:masto:", "")
  );
  const [whenRng, setWhenRng] = useState("7d");
  const [articles, setArticles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const fetchNews = (query = q, rng = whenRng) => {
    setLoading(true);
    setErr("");
    api
      .news(query || "social media misinformation", rng)
      .then((data) => {
        setArticles(data || []);
        if (!data || data.length === 0) {
          setErr("No headlines found for this query.");
        }
      })
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchNews(topic.replace("t3_", "").replace("bsky:", "").replace("masto:", "").replace("search:bsky:", "").replace("search:masto:", ""));
  }, [topic]);

  const onSearch = (e) => {
    e.preventDefault();
    fetchNews();
  };

  return (
    <div className="news-panel">
      <div className="news-header">
        <div>
          <h3>📰 Correlated Media & News Trends</h3>
          <p className="muted">
            Track broader news coverage related to viral cascade "{topic}"
          </p>
        </div>
        <button className="x-btn" onClick={onClose}>
          ✕ Close
        </button>
      </div>

      <form onSubmit={onSearch} className="news-form">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search news keyword…"
        />
        <select
          value={whenRng}
          onChange={(e) => {
            setWhenRng(e.target.value);
            fetchNews(q, e.target.value);
          }}
        >
          <option value="1d">Past 24 hours</option>
          <option value="7d">Past 7 days</option>
          <option value="1y">Past 1 year</option>
        </select>
        <button type="submit" disabled={loading}>
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      {err && <p className="msg error-msg">{err}</p>}

      <div className="news-list">
        {articles.map((a, i) => (
          <a
            key={i}
            href={a.link}
            target="_blank"
            rel="noopener noreferrer"
            className="news-card"
          >
            <h4>{a.title}</h4>
            <div className="news-meta">
              <span className="source-tag">{a.source || "News Source"}</span>
              <span className="date-tag">{a.date}</span>
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}

function Panel({ topic, name, onClose }) {
  const [u, setU] = useState(null);

  useEffect(() => {
    setU(null);
    api
      .user(topic, name)
      .then(setU)
      .catch(() => setU({ name, error: true }));
  }, [topic, name]);

  if (!u) {
    return (
      <aside className="panel">
        <p>Loading account details…</p>
      </aside>
    );
  }

  return (
    <aside className="panel">
      <button className="x" onClick={onClose}>
        Close
      </button>
      <h3>{u.name}</h3>
      {u.error ? (
        <p>No data for this account.</p>
      ) : (
        <>
          <p className="muted">
            {u.platform} · Main community: <strong>{u.community}</strong>
          </p>

          <div className="score-box">
            <div className="score-item">
              <span className="score-num">{u.bot_score}/100</span>
              <span className="score-lbl">Bot Likelihood</span>
            </div>
            <div
              className="score-item"
              title="Misinformation Risk: Average linguistic credibility score across recent comments based on sensationalist/alarmist phrasing (+0.25), uppercase shouting ratio > 45% (+0.20), and excessive exclamation/question punctuation (+0.15). Accounts with risk ≥ 50% are classified as high-risk."
            >
              <span className="score-num">
                {Math.round((u.misinfo_score || 0.15) * 100)}%
              </span>
              <span className="score-lbl">Misinformation Risk ℹ️</span>
            </div>
          </div>

          <dl>
            <dt>Posts count</dt>
            <dd>{u.posts}</dd>
            <dt>Replies sent / received</dt>
            <dd>
              {u.sent} / {u.received}
            </dd>
            <dt>Influence (PageRank)</dt>
            <dd>{u.influence}</dd>
            <dt>Active timeline</dt>
            <dd>
              {when(u.first)} to {when(u.last)}
            </dd>
            <dt>Community</dt>
            <dd>{u.community}</dd>
          </dl>

          <h4>Behavioral Engine Signals</h4>
          {u.signals && u.signals.length > 0 ? (
            <ul className="signals-list">
              {u.signals.map((sig, i) => (
                <li key={i} className="signal-item">
                  ⚠️ {sig}
                </li>
              ))}
            </ul>
          ) : (
            <p className="clean-signals">✓ No anomalous bot behavior detected</p>
          )}

          <h4>Path to Root (Cycle Detection)</h4>
          <ol className="trace-path">
            {u.trace && u.trace.length > 0 ? (
              u.trace.map((t, i) => (
                <li key={i}>
                  <strong>{t.author}</strong>{" "}
                  <span className="muted">[{t.community}]</span>
                </li>
              ))
            ) : (
              <li className="muted">Root post reached / no further parent</li>
            )}
          </ol>

          <h4>Recent message samples</h4>
          {u.samples && u.samples.length > 0 ? (
            u.samples.map((s, i) => (
              <blockquote key={i}>{s || "[Empty / media post]"}</blockquote>
            ))
          ) : (
            <p className="muted">No recent text content.</p>
          )}
        </>
      )}
    </aside>
  );
}

export default function App() {
  const [redditList, setRedditList] = useState([]);
  const [liveList, setLiveList] = useState([]);
  const [searchResultList, setSearchResultList] = useState([]);
  const [missingReddit, setMissingReddit] = useState(false);
  const [sel, setSel] = useState({ a: "", b: "" });
  const [cmp, setCmp] = useState(false);
  const [data, setData] = useState({});
  const [pick, setPick] = useState(null); // { side, name }
  const [msg, setMsg] = useState("");

  // Account search across ALL loaded data
  const [searchQuery, setSearchQuery] = useState("");
  const [searchMatches, setSearchMatches] = useState([]);
  const [showSearchDropdown, setShowSearchDropdown] = useState(false);
  const searchContainerRef = useRef(null);

  // Live fetch / Keyword search
  const [url, setUrl] = useState("");
  const [isFetchingLive, setIsFetchingLive] = useState(false);
  const [liveStatusText, setLiveStatusText] = useState("");

  // Filters for visualization
  const [filterIsolated, setFilterIsolated] = useState(false);
  const [isolateHighRisk, setIsolateHighRisk] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [showNews, setShowNews] = useState(false);

  // Neo4j state
  const [neo4jConnected, setNeo4jConnected] = useState(false);
  const [neo4jSyncing, setNeo4jSyncing] = useState(false);
  const [neo4jFeedback, setNeo4jFeedback] = useState({ type: "", text: "" });

  const checkNeo4j = async () => {
    try {
      const res = await api.neo4jStatus();
      setNeo4jConnected(Boolean(res && res.connected));
    } catch {
      setNeo4jConnected(false);
    }
  };

  useEffect(() => {
    checkNeo4j();
    const timer = setInterval(checkNeo4j, 10000);
    return () => clearInterval(timer);
  }, []);

  const syncToNeo4j = async () => {
    setNeo4jSyncing(true);
    setNeo4jFeedback({ type: "info", text: "Starting Neo4j sync…" });
    try {
      const res = await api.neo4jSync();
      if (res.skipped) {
        setNeo4jFeedback({
          type: "info",
          text: "✓ Nothing changed since last sync, skipped",
        });
        setNeo4jSyncing(false);
        return;
      }

      if (res.running) {
        // Poll sync status
        const pollTimer = setInterval(async () => {
          try {
            const st = await api.neo4jSyncStatus();
            if (st.running) {
              setNeo4jFeedback({
                type: "info",
                text: st.progress || "Syncing in background…",
              });
            } else {
              clearInterval(pollTimer);
              setNeo4jSyncing(false);
              setNeo4jConnected(true);
              const lr = st.last_result || {};
              if (lr.success) {
                setNeo4jFeedback({
                  type: "success",
                  text: lr.message || "Neo4j sync complete",
                });
              } else {
                setNeo4jFeedback({
                  type: "error",
                  text: lr.message || "Sync failed",
                });
              }
            }
          } catch {
            clearInterval(pollTimer);
            setNeo4jSyncing(false);
          }
        }, 1200);
      } else if (res.success) {
        setNeo4jConnected(true);
        setNeo4jFeedback({
          type: "success",
          text: `Synced ${res.nodes_synced?.toLocaleString() || 0} users, ${res.edges_synced?.toLocaleString() || 0} edges`,
        });
        setNeo4jSyncing(false);
      } else {
        setNeo4jFeedback({
          type: "error",
          text: res.message || "Sync failed",
        });
        setNeo4jSyncing(false);
      }
    } catch (err) {
      setNeo4jFeedback({
        type: "error",
        text: err.message || "Sync failed",
      });
      setNeo4jSyncing(false);
    }
  };

  const refresh = async () => {
    try {
      const res = await api.topics();
      if (res.error === "Run fetch_reddit.py first") {
        setMissingReddit(true);
        setMsg("Run fetch_reddit.py first");
        setRedditList([]);
        setLiveList(res.live || []);
        setSearchResultList(res.search_results || []);
        return;
      }
      setMissingReddit(false);
      const rList = res.reddit || (Array.isArray(res) ? res.filter((t) => t.platform === "reddit") : []);
      const lList = res.live || (Array.isArray(res) ? res.filter((t) => t.platform !== "reddit") : []);
      const sList = res.search_results || [];
      setRedditList(rList);
      setLiveList(lList);
      setSearchResultList(sList);
      setSel((s) => ({
        a: s.a || rList[0]?.topic || lList[0]?.topic || sList[0]?.topic || "",
        b: s.b || rList[1]?.topic || rList[0]?.topic || lList[0]?.topic || sList[0]?.topic || "",
      }));
    } catch (err) {
      setMsg(err.message || "Run fetch_reddit.py first");
      if (err.message && err.message.includes("Run fetch_reddit.py first")) {
        setMissingReddit(true);
      }
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  // Close search dropdown on click outside
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (searchContainerRef.current && !searchContainerRef.current.contains(e.target)) {
        setShowSearchDropdown(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Load graph data for topics
  useEffect(() => {
    ["a", "b"].forEach((k) => {
      if (sel[k]) {
        // If pick is currently on this side, ensure picked account is included even if outside top 300
        const includeUser = pick?.side === k ? pick.name : null;
        api
          .analysis(sel[k], showAll ? 0 : 300, includeUser)
          .then((d) => setData((o) => ({ ...o, [k]: d })))
          .catch((e) => setMsg(e.message));
      }
    });
  }, [sel, showAll]);

  const sides = cmp ? ["a", "b"] : ["a"];

  // Handle Account Search Input (debounced live lookup across ALL loaded data)
  useEffect(() => {
    const qTrim = searchQuery.trim();
    if (!qTrim) {
      setSearchMatches([]);
      setShowSearchDropdown(false);
      return;
    }

    const timer = setTimeout(async () => {
      try {
        const res = await api.accountSearch(qTrim);
        setSearchMatches(res.matches || []);
        setShowSearchDropdown(true);
      } catch {
        setSearchMatches([]);
      }
    }, 150);

    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Handle picking an account search match
  const handlePickAccountMatch = async (match) => {
    setShowSearchDropdown(false);
    setSearchQuery(match.name);
    setMsg(`Account found: ${match.name} (switching to thread: ${match.best_thread})`);

    // 1. Switch to the thread where that account has the most comments
    setSel((s) => ({ ...s, a: match.best_thread }));

    // 2. Open ITS panel (never keep previous account's panel)
    setPick({ side: "a", name: match.name });

    // 3. Add the account and its direct neighbours to the view even if outside the top 300
    try {
      const threadData = await api.analysis(match.best_thread, showAll ? 0 : 300, match.name);
      setData((o) => ({ ...o, a: threadData }));
    } catch (e) {
      setMsg(e.message);
    }
  };

  const handleSearchSubmit = async (e) => {
    e.preventDefault();
    const qTrim = searchQuery.trim();
    if (!qTrim) return;

    try {
      const res = await api.accountSearch(qTrim);
      if (res.matches && res.matches.length > 0) {
        handlePickAccountMatch(res.matches[0]);
      } else {
        setShowSearchDropdown(false);
        // If not found, say which source and thread were searched
        setMsg(res.message || `No account matching "${qTrim}" found.`);
      }
    } catch (err) {
      setMsg(err.message || `Search failed for "${qTrim}"`);
    }
  };

  // Live Fetch & Keyword Search dispatch
  const handleLiveSubmit = async (e) => {
    e.preventDefault();
    const inputVal = url.trim();
    if (!inputVal) return;

    setIsFetchingLive(true);
    setLiveStatusText(
      inputVal.toLowerCase().startsWith("http")
        ? "Fetching live social cascade…"
        : `Searching Bluesky and Mastodon for "${inputVal}"…`
    );
    setMsg("");

    // Optional status polling for keywords
    let pollInterval = null;
    if (!inputVal.toLowerCase().startsWith("http")) {
      pollInterval = setInterval(async () => {
        try {
          const st = await api.fetchStatus(inputVal);
          if (st && st.text) setLiveStatusText(st.text);
        } catch {
          // ignore polling error
        }
      }, 1500);
    }

    try {
      const res = await api.fetchLive(inputVal);
      if (pollInterval) clearInterval(pollInterval);
      await refresh();

      if (res.type === "link") {
        setSel((s) => ({ ...s, a: res.topic }));
        setMsg(`Successfully ingested ${res.posts} posts from ${res.topic}`);
      } else if (res.type === "search") {
        if (res.biggest_topic) {
          setSel((s) => ({ ...s, a: res.biggest_topic }));
        }
        setMsg(
          res.message +
            (res.warning ? ` (${res.warning})` : "")
        );
      }
      setUrl("");
    } catch (err) {
      if (pollInterval) clearInterval(pollInterval);
      setMsg(err.message || "Failed to process live fetch / keyword search");
    } finally {
      setIsFetchingLive(false);
      setLiveStatusText("");
    }
  };

  return (
    <div className="app">
      <header>
        <div className="brand">
          <div className="brand-icon">🕸️</div>
          <div>
            <h1>NodeZero</h1>
            <span className="tagline">Misinformation Cascade Tracker</span>
          </div>
        </div>

        {/* Global Account Search across ALL loaded data */}
        <div className="search-wrapper" ref={searchContainerRef}>
          <form onSubmit={handleSearchSubmit} className="search">
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onFocus={() => {
                if (searchMatches.length > 0) setShowSearchDropdown(true);
              }}
              placeholder="Search ALL accounts (case-insensitive, top 10 matches)…"
            />
            <button type="submit" className="search-btn">🔍</button>
          </form>

          {showSearchDropdown && searchMatches.length > 0 && (
            <ul className="search-dropdown">
              {searchMatches.map((m) => (
                <li
                  key={m.name}
                  className="search-item"
                  onClick={() => handlePickAccountMatch(m)}
                >
                  <div className="search-item-header">
                    <strong>{m.name}</strong>
                    <span className="search-item-total">{m.total_comments} comments</span>
                  </div>
                  <div className="search-item-sub">
                    Primary thread: <code>{m.best_thread}</code> ({m.comments_in_best} comments)
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="header-actions">
          <div className="neo4j-group">
            <span
              className={`neo4j-badge ${neo4jConnected ? "connected" : "disconnected"}`}
              title={neo4jConnected ? "Neo4j database connected" : "Neo4j database disconnected"}
            >
              Neo4j: {neo4jConnected ? "connected" : "disconnected"}
            </span>
            <button
              type="button"
              className="neo4j-btn"
              disabled={neo4jSyncing}
              onClick={syncToNeo4j}
              title="Sync current dataset into Neo4j graph in batches of 5000"
            >
              {neo4jSyncing ? "Syncing…" : "Sync to Neo4j"}
            </button>
            {neo4jFeedback.text && (
              <span className={`neo4j-msg ${neo4jFeedback.type}`}>
                {neo4jFeedback.text}
              </span>
            )}
          </div>

          <button
            type="button"
            className={`btn-pill ${showNews ? "active" : ""}`}
            onClick={() => setShowNews(!showNews)}
            title="Media Trends: Correlates viral cascades with mainstream Google News RSS coverage across customizable timeframes (1d, 7d, 1y)."
          >
            📰 Media Trends
          </button>
          <label className="cmp-toggle">
            <input
              type="checkbox"
              checked={cmp}
              onChange={(e) => setCmp(e.target.checked)}
            />{" "}
            Compare two topics
          </label>
        </div>
      </header>

      {/* Missing reddit.csv banner */}
      {missingReddit && (
        <div className="missing-data-banner">
          ⚠️ Run fetch_reddit.py first
        </div>
      )}

      {/* Live Fetch & Keyword Search Box */}
      <form onSubmit={handleLiveSubmit} className="live">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={isFetchingLive}
          placeholder="Live fetch: paste Bluesky/Mastodon link (https://...) or enter keywords to search (e.g., vpn, nepal flood, vaccine)…"
        />
        <button disabled={!url || isFetchingLive}>
          {isFetchingLive ? "Searching…" : "Fetch live"}
        </button>
        {isFetchingLive && <span className="msg live-progress">{liveStatusText}</span>}
        {!isFetchingLive && msg && <span className="msg">{msg}</span>}
      </form>

      {/* Embedded News Headlines Panel */}
      {showNews && sel.a && (
        <NewsPanel topic={sel.a} onClose={() => setShowNews(false)} />
      )}

      {/* Visualization Filtering Controls */}
      <div className="controls-bar">
        <div className="controls-group">
          <span className="controls-label">Cascade Filters:</span>
          <label className="filter-checkbox">
            <input
              type="checkbox"
              checked={filterIsolated}
              onChange={(e) => setFilterIsolated(e.target.checked)}
            />
            Filter out isolated nodes
          </label>
          <label
            className="filter-checkbox highlight-risk"
            title="Isolate high-risk misinformation clusters: Filters the graph to show only flagged bots (bot score ≥ 60) or questionable/misinformation content (risk ≥ 40%), plus their direct 1-hop reply neighbors."
          >
            <input
              type="checkbox"
              checked={isolateHighRisk}
              onChange={(e) => setIsolateHighRisk(e.target.checked)}
            />
            🛡️ Isolate high-risk misinformation clusters
          </label>
          <label className="filter-checkbox">
            <input
              type="checkbox"
              checked={showAll}
              onChange={(e) => setShowAll(e.target.checked)}
            />
            Show all accounts (default: top 300 by influence)
          </label>
        </div>
      </div>

      <main className={cmp ? "two" : ""}>
        {sides.map((k) => (
          <section key={k}>
            <div className="topic-select-row">
              <label className="selector-label">
                Viral Thread:
              </label>
              <select
                value={sel[k]}
                onChange={(e) => setSel({ ...sel, [k]: e.target.value })}
              >
                {redditList.length > 0 && (
                  <optgroup label="Reddit">
                    {redditList.map((t) => (
                      <option key={t.topic} value={t.topic}>
                        {t.topic} ({t.comments} comments)
                      </option>
                    ))}
                  </optgroup>
                )}
                {liveList.length > 0 && (
                  <optgroup label="Live">
                    {liveList.map((t) => (
                      <option key={t.topic} value={t.topic}>
                        {t.topic} ({t.comments} posts)
                      </option>
                    ))}
                  </optgroup>
                )}
                {searchResultList.length > 0 && (
                  <optgroup label="Search results">
                    {searchResultList.map((t) => (
                      <option key={t.topic} value={t.topic}>
                        {t.label || `${t.topic.slice(0, 50)} - ${t.platform} - ${t.comments} replies`}
                      </option>
                    ))}
                  </optgroup>
                )}
              </select>
            </div>

            {data[k] && (
              <>
                <Summary
                  s={data[k].summary}
                  onRoot={(n) => setPick({ side: k, name: n })}
                  onFilterFlagged={() => {
                    const fl = data[k].nodes.find((n) => n.flagged);
                    if (fl) setPick({ side: k, name: fl.id });
                    setIsolateHighRisk(true);
                  }}
                />
                <Graph
                  data={data[k]}
                  focus={pick?.side === k ? pick.name : null}
                  onPick={(n) => setPick({ side: k, name: n })}
                  filterIsolated={filterIsolated}
                  isolateHighRisk={isolateHighRisk}
                />
              </>
            )}
          </section>
        ))}
      </main>

      {pick && data[pick.side] && (
        <Panel
          topic={data[pick.side].topic}
          name={pick.name}
          onClose={() => setPick(null)}
        />
      )}
    </div>
  );
}
