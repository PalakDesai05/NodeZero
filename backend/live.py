# One search across Bluesky + Mastodon (Mastodon fills in when Bluesky has little). Duplicates removed.
import os, re, time, requests, datetime as dt
import pandas as pd
import bsky, youtube as yt
from common import save, D

STOP = set('the a an of to in on at for and or with from by is are was were be as that this it its his her their who what when how why about after over into than then he she they you we has have had will would said says say news latest personally'.split())
I = os.getenv('MASTODON_INSTANCE', 'https://mastodon.social')
clean = lambda h: re.sub(r'<[^>]+>', '', h).replace('\n', ' ')
mts = lambda s: int(dt.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp())
day = lambda s, end=0: dt.datetime.strptime(s, '%Y-%m-%d').replace(tzinfo=dt.timezone.utc).timestamp() + (86399 if end else 0)
norm = lambda t: re.sub(r'\W+', '', t.lower())

def variants(q):
    """Full query first, then shorter keyword versions (long sentences rarely match exactly)."""
    ws = list(dict.fromkeys(w for w in re.findall(r"\w+", q.lower()) if w not in STOP and len(w) > 2))
    cap = {w.lower() for w in re.findall(r"\b[A-Z]\w+", q)}
    pri = sorted([w for w in ws if w in cap] or ws, key=len, reverse=True)
    vs = [q] + [' '.join(pri[:n]) for n in (3, 2, 1) if pri[:n]]
    return list(dict.fromkeys(vs)), pri

def mget(p, **q):
    H = {'Authorization': 'Bearer ' + os.environ['MASTODON_TOKEN']} if os.getenv('MASTODON_TOKEN') else {}
    try: r = requests.get(f'{I}/api/{p}', params=q, headers=H, timeout=20).json()
    except Exception: return None
    return None if isinstance(r, dict) and 'error' in r else r

def mastodon(tags, q):
    rows = {}
    def add(s, root):
        s = s.get('reblog') or s
        a = s['account']
        rows[s['id']] = [s['id'], a['acct'], s['in_reply_to_id'] or '', root, clean(s['content']), mts(s['created_at']),
                         a.get('followers_count', ''), a.get('following_count', ''), a.get('statuses_count', ''),
                         mts(a['created_at']) if a.get('created_at') else '', a.get('bot', '')]
    def with_replies(s, n):
        s = s.get('reblog') or s
        add(s, s['in_reply_to_id'] or s['id'])
        if s['replies_count'] and n[0] < 15:
            n[0] += 1; time.sleep(.3)
            for d in (mget(f"v1/statuses/{s['id']}/context") or {}).get('descendants', []): add(d, s['id'])
    n = [0]
    if os.getenv('MASTODON_TOKEN'):  # full-text search needs a login
        for s in (mget('v2/search', q=q, type='statuses', limit=40, resolve='false') or {}).get('statuses', []): with_replies(s, n)
    for t in tags:
        mx = None
        for _ in range(2):
            page = mget(f'v1/timelines/tag/{t}', limit=40, max_id=mx)
            if not page: break
            mx = page[-1]['id']
            for s in page: with_replies(s, n)
    return rows

def reddit(kws, since='', until=''):
    """Search the downloaded Reddit dataset (data/reddit.csv) and pull in the whole threads of matching comments."""
    p = f'{D}/reddit.csv'
    if not os.path.exists(p): return {}, 'Reddit dataset not downloaded yet (run fetch_reddit.py)'
    df = pd.read_csv(p, dtype=str).fillna('')
    if since or until:
        t = pd.to_numeric(df.created_utc, errors='coerce').fillna(0)
        df = df[(t >= (day(since) if since else 0)) & (t <= (day(until, 1) if until else 1e13))]
    low = df.body.str.lower()
    hits = sum(low.str.contains(re.escape(k), regex=True).astype(int) for k in kws) if kws else 0
    m = df[hits >= (2 if len(kws) > 1 else 1)] if kws else df.iloc[0:0]
    if m.empty and kws: m = df[low.str.contains(re.escape(kws[0]), regex=True)]
    if m.empty: return {}, ''
    t = pd.concat([m, df[df.root_id.isin(set(m.root_id))]]).drop_duplicates('id').head(600)
    return {r.id: [r.id, r.author, r.parent_id, r.root_id, r.body, r.created_utc] for r in t.itertuples()}, ''

def youtube(q, since='', until=''):
    if not os.getenv('YOUTUBE_API_KEY'): return {}, 'YouTube skipped: set YOUTUBE_API_KEY'
    try:
        link = re.search(r'youtu\.?be', q) and yt.video_id(q)
        rows = {}
        for v in ([link] if link else yt.search(q, 3, since, until)): rows.update(yt.comments(v, 300))
        return rows, ''
    except Exception as e: return {}, f'YouTube failed: {e}'

def search(q, since='', until=''):
    vs, pri = variants(q); tags = pri[:4]; b, m, errs = {}, {}, []
    if re.search(r'youtu\.?be', q):  # a pasted YouTube link: only that video
        y, err = youtube(q, since, until)
        if err: errs.append(err)
        rows = [r for r in y.values() if r[4].strip()]
        if rows: save('search', rows)
        return {'posts': len(rows), 'bluesky': 0, 'mastodon': 0, 'reddit': 0, 'youtube': len(rows), 'duplicates_removed': 0, 'errors': errs}
    try:
        for v in vs:
            b.update(bsky.fetch(v, since, until))
            if len(b) >= 60: break
    except PermissionError as e: errs.append(str(e))
    except Exception as e: errs.append(f'Bluesky failed: {e}')
    m = mastodon(tags, q)
    if since or until:
        lo, hi = day(since) if since else 0, day(until, 1) if until else 1e13
        m = {k: r for k, r in m.items() if lo <= r[5] <= hi}
    if not m: errs.append('Mastodon returned nothing (set MASTODON_TOKEN or try MASTODON_INSTANCE)')
    r, err = reddit(pri, since, until)
    if err: errs.append(err)
    y, err = youtube(q, since, until)
    if err: errs.append(err)
    # Same post on several networks -> keep the first copy (Bluesky > Mastodon > Reddit) and re-point replies to it.
    # Repeats inside one network are kept on purpose: repeated text is a bot signal.
    seen, alias, rows, cnt = {}, {}, [], {'bluesky': 0, 'mastodon': 0, 'reddit': 0, 'youtube': 0}
    for name, src in (('bluesky', b), ('mastodon', m), ('reddit', r), ('youtube', y)):
        new = {}
        for row in src.values():
            k = norm(row[4])
            if not row[4].strip(): continue
            if len(k) >= 40 and k in seen: alias[row[0]] = seen[k]; continue
            rows.append(row); cnt[name] += 1
            if len(k) >= 40: new[k] = row[0]
        seen.update(new)
    for row in rows: row[2], row[3] = alias.get(row[2], row[2]), alias.get(row[3], row[3])
    for row in rows: row += [''] * (11 - len(row))
    if rows: save('search', rows)
    return {'posts': len(rows), **cnt, 'duplicates_removed': len(alias), 'errors': errs}
