import re, pandas as pd, networkx as nx
from detector import score

BOT = 60  # bot score (0-100) at or above this = flagged (i.e. at least two of the three signals)
class S: df = g = pr = m = lo = bot = why = mis = recs = first = None; scored = False

lab = lambda x: 'Likely misinformation' if x > .5 else 'Likely reliable'
pct = lambda x: f'{x:.0%}'
infl = lambda n: 0.0 if S.m == S.lo else (S.pr[n] - S.lo) / (S.m - S.lo)  # 0 for accounts nobody replied to
short = lambda n: n if len(n) <= 18 else '…' + n[-10:]

def load(path):
    df = pd.read_csv(path, dtype=str).fillna('')
    for c in ('parent_id', 'root_id'):
        df[c] = df[c].str.replace(r'^t\d_', '', regex=True)
    # deleted authors stay in the id->row lookup (so parent chains don't break); each one is its own node, not one big hub
    dead = df.author.isin(['[deleted]', '[removed]', ''])
    df.loc[dead, 'author'] = 'deleted:' + df.id[dead]
    df['t'] = pd.to_numeric(df.created_utc, errors='coerce').fillna(0)
    df['misinfo'] = 0.0
    a = dict(zip(df.id, df.author))
    def to(r):
        p = r.parent_id
        if not p: return ''
        return a.get(p) or (p.split('/')[2] if p.startswith('at://') else 'root:' + r.root_id if r.root_id else '')
    df['to'] = df.apply(to, axis=1)
    g = nx.DiGraph(); g.add_nodes_from(df.author.unique())
    for r in df.itertuples():
        if r.to and r.to != r.author:
            e = g.get_edge_data(r.author, r.to)
            g.add_edge(r.author, r.to, w=e['w'] + 1 if e else 1, t=min(e['t'], r.t) if e else r.t)
    pr = nx.pagerank(g, weight='w')
    # Bot-likelihood heuristic (0-100): reply volume > 95th percentile (+40), username digit ratio > 20% (+30), out/in-degree ratio > 3 (+30)
    vol = df.author.value_counts(); q95 = vol.quantile(.95)
    def bot(u):
        if u.startswith('deleted:'): return 0, []
        s, why = 0, []
        if vol[u] > q95: s += 40; why.append('reply volume in the top 5%')
        name = u.split('@')[0]
        if not u.startswith('did:') and sum(c.isdigit() for c in name) / max(len(name), 1) > .2: s += 30; why.append('username is mostly digits')
        if g.out_degree(u, weight='w') / max(g.in_degree(u, weight='w'), 1) > 3: s += 30; why.append('replies far more than it receives')
        return s, why
    res = {u: bot(u) for u in df.author.unique()}
    S.bot, S.why = {u: v[0] for u, v in res.items()}, {u: v[1] for u, v in res.items()}
    S.first = df.groupby('author').t.min().to_dict()
    for u, v, d in g.edges(data=True): S.first[v] = min(S.first.get(v, d['t']), d['t'])
    S.df, S.g, S.pr, S.m, S.lo, S.mis, S.scored = df, g, pr, max(pr.values()), min(pr.values()), {}, False
    S.recs = {r['id']: r for r in df.to_dict('records')}
    return {'users': int(df.author.nunique()), 'replies': len(df), 'threads': int(df.root_id.nunique()),
            'flagged_bots': sum(v >= BOT for v in S.bot.values())}

def score_all():
    """Experimental: run the small AI detector over every post."""
    S.df['misinfo'] = score(S.df.body)
    S.mis = S.df.groupby('author').misinfo.mean().to_dict()
    for n in S.g.nodes:
        if n.startswith('root:'): S.mis[n] = S.df[S.df.root_id == n[5:]].misinfo.mean()
    S.recs = {r['id']: r for r in S.df.to_dict('records')}
    S.scored = True
    return {'scored': len(S.df)}

def nd(n, **kw):
    x = dict(id=n, label=short(n), type='root' if n.startswith('root:') else 'user', influence=round(infl(n), 3),
             bot=S.bot.get(n, 0), misinfo=round(S.mis.get(n, 0), 3), t=float(S.first.get(n, 0)))
    return {**x, **kw}

def graph_json(limit=300):
    keep = set(sorted(S.pr, key=S.pr.get, reverse=True)[:limit])
    return {'nodes': [nd(n) for n in keep],
            'links': [dict(source=u, target=v, t=float(d['t'])) for u, v, d in S.g.edges(data=True) if u in keep and v in keep]}

def top(kind, k=10):
    d = {u: infl(u) for u in S.pr} if kind == 'inf' else S.bot
    d = {u: v for u, v in d.items() if not u.startswith('root:')}
    return [{'user': u, 'score': round(v, 2)} for u, v in sorted(d.items(), key=lambda x: -x[1])[:k]]

# ---- thread cascades -------------------------------------------------------------------------------------------
def _edges(d):
    """Spread direction: parent account -> replying account, with reply count and first time."""
    E = {}
    for r in d.itertuples():
        if r.to and r.to != r.author:
            e = E.get((r.to, r.author))
            E[(r.to, r.author)] = (e[0] + 1, min(e[1], r.t)) if e else (1, r.t)
    return E

def _rank(E, ids):
    g = nx.DiGraph(); g.add_nodes_from(ids)
    for (u, v), (w, t) in E.items(): g.add_edge(v, u, w=w)  # PageRank flows replier -> replied-to
    pr = nx.pagerank(g, weight='w'); lo, hi = min(pr.values()), max(pr.values())
    return {n: 0.0 if hi == lo else (p - lo) / (hi - lo) for n, p in pr.items()}

def _depths(d):
    rec = {r.id: r.parent_id for r in d.itertuples()}
    def dep(i):
        seen, n = set(), 0
        while i in rec and i not in seen and n < 60: seen.add(i); i = rec[i]; n += 1
        return n
    per = {r.id: dep(r.id) for r in d.itertuples()}
    out = {}
    for r in d.itertuples(): out[r.author] = min(out.get(r.author, 99), per[r.id])
    return out, (max(per.values()) if per else 0)

def threads(k=20):
    out = []
    for rid, n in S.df.root_id.value_counts().head(k + 1).items():
        if not rid: continue
        d = S.df[S.df.root_id == rid]
        text = S.recs[rid]['body'] if rid in S.recs else d.sort_values('t').iloc[0].body
        out.append({'root_id': rid, 'replies': int(n), 'users': int(d.author.nunique()), 'snippet': (text[:60] + '…') if len(text) > 60 else text or rid})
    return out[:k]

def cascade(rid):
    d = S.df[S.df.root_id == rid]
    if d.empty: raise KeyError(rid)
    E = _edges(d)
    ids = set(d.author) | {n for e in E for n in e}
    inf = _rank(E, ids)
    if len(ids) > 300: ids = set(sorted(ids, key=lambda n: -inf[n])[:300])
    dep, _ = _depths(d); first = d.groupby('author').t.min().to_dict(); lt = {}
    for (u, v), (w, t) in E.items():
        for n in (u, v): lt[n] = min(lt.get(n, t), t)
    nodes = [nd(n, influence=round(inf[n], 3), depth=dep.get(n, 0),
                t=float(first.get(n, lt.get(n, 1) - (1 if n.startswith('root:') else 0)))) for n in ids]
    links = [dict(source=u, target=v, t=float(t)) for (u, v), (w, t) in E.items() if u in ids and v in ids]
    return {'graph': {'nodes': nodes, 'links': links}, 'info': root(rid)}

def user(u):
    if u not in S.g: raise KeyError(u)
    d = S.df[S.df.author == u]
    out = {'user': u, 'influence': round(infl(u), 2), 'bot_score': S.bot.get(u, 0), 'bot_signals': S.why.get(u) or ['None'],
           'replies_written': len(d), 'replies_received': int(S.g.in_degree(u, weight='w')), 'threads': int(d.root_id.nunique()),
           'replying_to': [f'{k} ({v})' for k, v in d.to.value_counts().head(3).items()]}
    if S.scored:
        m = S.mis.get(u, 0)
        out.update(avg_misinformation=pct(m), verdict=lab(m), riskiest_posts=[f'{pct(r.misinfo)}  {r.body[:120]}' for r in d.nlargest(3, 'misinfo').itertuples()])
    return out

def root(rid):
    d = S.df[S.df.root_id == rid]
    if d.empty: raise KeyError(rid)
    text = S.recs[rid]['body'] if rid in S.recs else d.sort_values('t').iloc[0].body
    out = {'opening_post': text[:120] + ('…' if len(text) > 120 else ''), 'thread_id': short(rid), 'replies': len(d), 'users': int(d.author.nunique()), 'deepest_chain': _depths(d)[1],
           'top_commenters': [f'{k} ({v})' for k, v in d.author.value_counts().head(5).items()],
           'likely_bots': [u for u in d.author.unique() if S.bot.get(u, 0) >= BOT] or ['None']}
    if S.scored: m = d.misinfo.mean(); out.update(avg_misinformation=pct(m), verdict=lab(m))
    return out

def trace(author):
    """Hop-by-hop walk from an account's latest reply up to the root (max 50 hops, stops on a cycle)."""
    d = S.df[S.df.author == author]
    if d.empty: raise KeyError(author)
    cur, seen, path, who = S.recs[d.sort_values('t').iloc[-1].id], set(), [], []
    cycle = False
    while cur and len(path) < 50:
        if cur['id'] in seen: cycle = True; break
        seen.add(cur['id']); who.append(cur['author'])
        path.append(f"{cur['author']}: {cur['body'][:80]}" + (f" [{pct(cur['misinfo'])}]" if S.scored else ''))
        cur = S.recs.get(cur['parent_id'])
    return {'root_thread': S.recs[d.iloc[0].id]['root_id'], 'hops': len(path), 'cycle_detected': cycle, 'path': path, 'authors': who}

def summary(rid=''):
    df = S.df; d = df[df.root_id == rid] if rid else df
    if d.empty: raise KeyError(rid)
    E = _edges(d); ids = set(d.author) | {n for e in E for n in e}
    inf = _rank(E, ids) if rid else {n: infl(n) for n in ids if n in S.pr}
    spread = [n for n in sorted(inf, key=lambda n: -inf[n]) if not n.startswith('root:')]
    users = set(d.author); bots = sorted(((u, S.bot.get(u, 0)) for u in users if S.bot.get(u, 0) >= 30), key=lambda x: -x[1])[:6]
    out = {'root_account': d.sort_values('t').iloc[0].author, 'top_spreader': spread[0] if spread else '', 'accounts_checked': len(users),
           'flagged_accounts': [u for u in users if S.bot.get(u, 0) >= BOT],
           'bots': [{'user': u, 'score': v, 'why': ', '.join(S.why[u])} for u, v in bots], 'misinfo': None}
    if S.scored:
        m = d.misinfo > .5; share = m.mean()
        out['misinfo'] = {'level': 'High' if share >= .5 else 'Medium' if share >= .25 else 'Low', 'share': pct(share), 'posts': int(m.sum()), 'total': len(d),
                          'riskiest': [{'user': r.author, 'score': pct(r.misinfo), 'text': r.body[:140]} for r in d.nlargest(3, 'misinfo').itertuples()]}
    return out
