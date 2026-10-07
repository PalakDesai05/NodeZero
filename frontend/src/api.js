const API = import.meta.env.VITE_API || "http://127.0.0.1:8000";
const j = async (r) => {
  if (!r.ok) {
    const errObj = await r.json().catch(() => ({}));
    throw new Error(errObj.detail || errObj.message || r.statusText);
  }
  return r.json();
};
const e = encodeURIComponent;

export const topics = () => fetch(`${API}/topics`).then(j);

export const analysis = (t, limit = 300, includeUser = null) => {
  const params = new URLSearchParams();
  if (limit !== undefined && limit !== null) params.set("limit", limit);
  if (includeUser) params.set("include_user", includeUser);
  const qStr = params.toString() ? `?${params.toString()}` : "";
  return fetch(`${API}/analysis/${e(t)}${qStr}`).then(j);
};

export const user = (t, n) => fetch(`${API}/user/${e(t)}/${e(n)}`).then(j);

export const accountSearch = (q) => fetch(`${API}/accounts/search?q=${e(q)}`).then(j);

export const fetchLive = (text) =>
  fetch(`${API}/fetch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: text, query: text }),
  }).then(j);

export const fetchStatus = (q) => fetch(`${API}/fetch/status?q=${e(q)}`).then(j);

export const news = (q, when = "7d") => fetch(`${API}/news?q=${e(q)}&when=${e(when)}`).then(j);

export const classify = (text) => fetch(`${API}/classify?text=${e(text)}`).then(j);

export const neo4jStatus = () => fetch(`${API}/neo4j/status`).then(j);

export const neo4jSync = () => fetch(`${API}/neo4j/sync`, { method: "POST" }).then(j);

export const neo4jSyncStatus = () => fetch(`${API}/neo4j/sync/status`).then(j);

export const neo4jCheck = () => fetch(`${API}/neo4j/check`).then(j);
