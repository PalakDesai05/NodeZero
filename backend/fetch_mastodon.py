# Usage: python fetch_mastodon.py            -> auto-fetches whatever is trending right now (all topics)
#        python fetch_mastodon.py tag1,tag2  -> only those hashtags
# Optional env: MASTODON_INSTANCE (default https://mastodon.social), MASTODON_TOKEN (if your instance needs login)
import os, re, sys, time, requests, datetime as dt
from common import save
I = os.getenv('MASTODON_INSTANCE', 'https://mastodon.social')
H = {'Authorization': 'Bearer ' + os.environ['MASTODON_TOKEN']} if os.getenv('MASTODON_TOKEN') else {}
clean = lambda h: re.sub(r'<[^>]+>', '', h).replace('\n', ' ')
ts = lambda s: int(dt.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp())
rows = {}

def get(p, **q):
    try: r = requests.get(f'{I}/api/v1/{p}', params=q, headers=H, timeout=20).json()
    except Exception as e: print('request failed:', e); return None
    if isinstance(r, dict) and 'error' in r: print('API:', r['error']); return None
    return r

def add(s, root):
    rows[s['id']] = [s['id'], s['account']['acct'], s['in_reply_to_id'] or '', root, clean(s['content']), ts(s['created_at'])]

def thread(s):
    s = s.get('reblog') or s
    if s['replies_count'] == 0 or s['id'] in rows: return
    add(s, s['id']); time.sleep(.5)
    for d in (get(f"statuses/{s['id']}/context") or {}).get('descendants', []): add(d, s['id'])

tags = sys.argv[1].split(',') if len(sys.argv) > 1 else [t['name'] for t in get('trends/tags', limit=20) or []]
print('hashtags:', tags)
if len(sys.argv) == 1:
    for s in (get('trends/statuses', limit=40) or []) + (get('timelines/public', limit=40) or []): thread(s)
for t in tags:
    mx = None
    for _ in range(3):
        page = get(f'timelines/tag/{t}', limit=40, max_id=mx)
        if not page: break
        mx = page[-1]['id']
        for s in page: thread(s)
    print(t, len(rows))
save('mastodon', list(rows.values())) if rows else print('Nothing fetched. If you saw a 401, set MASTODON_TOKEN.')
