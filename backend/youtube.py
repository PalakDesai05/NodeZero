# YouTube comments via the official YouTube Data API v3 (free key: Google Cloud Console > enable "YouTube Data API v3" > Credentials > API key).
# Set env YOUTUBE_API_KEY. Note: YouTube replies are one level deep (a reply to a reply is shown under the same top comment).
import os, re, requests, datetime as dt
API = 'https://www.googleapis.com/youtube/v3'
ts = lambda s: int(dt.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp())

def video_id(x):
    m = re.search(r'(?:v=|youtu\.be/|shorts/|embed/)([\w-]{11})', x)
    return m.group(1) if m else (x if re.fullmatch(r'[\w-]{11}', x) else None)

def _get(path, **p):
    r = requests.get(f'{API}/{path}', params={**p, 'key': os.environ['YOUTUBE_API_KEY']}, timeout=30)
    j = r.json()
    if r.status_code != 200: raise RuntimeError(j.get('error', {}).get('message', r.text)[:200])
    return j

def search(q, n=3, since='', until=''):
    p = {'part': 'snippet', 'q': q, 'type': 'video', 'order': 'relevance', 'maxResults': n}
    if since: p['publishedAfter'] = since + 'T00:00:00Z'
    if until: p['publishedBefore'] = until + 'T23:59:59Z'
    return [i['id']['videoId'] for i in _get('search', **p).get('items', [])]

def comments(vid, limit=400):
    """{id: row}. Top-level comments hang off the video (the root), replies hang off their top comment."""
    rows = {}
    def add(c, parent):
        s = c['snippet']
        rows[c['id']] = [c['id'], s.get('authorDisplayName', ''), parent, vid, s.get('textDisplay', '').replace('\n', ' '), ts(s['publishedAt'])]
    tok = None
    while len(rows) < limit:
        j = _get('commentThreads', part='snippet,replies', videoId=vid, maxResults=100, order='relevance', textFormat='plainText', **({'pageToken': tok} if tok else {}))
        for t in j.get('items', []):
            top = t['snippet']['topLevelComment']; add(top, vid)
            got = t.get('replies', {}).get('comments', [])
            for c in got: add(c, top['id'])
            if t['snippet']['totalReplyCount'] > len(got):  # the API only inlines a few replies, fetch the rest
                rt = None
                while True:
                    k = _get('comments', part='snippet', parentId=top['id'], maxResults=100, textFormat='plainText', **({'pageToken': rt} if rt else {}))
                    for c in k.get('items', []): add(c, top['id'])
                    rt = k.get('nextPageToken')
                    if not rt or len(rows) >= limit: break
        tok = j.get('nextPageToken')
        if not tok: break
    return rows
