# Bluesky topic search (needs free account + app password: bsky.app > Settings > Privacy and security > App passwords)
# Set env BSKY_HANDLE and BSKY_APP_PASSWORD before starting the backend.
import os, time, requests, datetime as dt
B = 'https://bsky.social/xrpc'
ts = lambda s: int(dt.datetime.fromisoformat((s or '1970-01-01T00:00:00Z').replace('Z', '+00:00')).timestamp())

def fetch(q, since='', until='', threads=20):
    """Returns {post_uri: row} for posts matching q plus their reply threads."""
    if not (os.getenv('BSKY_HANDLE') and os.getenv('BSKY_APP_PASSWORD')):
        raise PermissionError('Bluesky skipped: set BSKY_HANDLE and BSKY_APP_PASSWORD, then restart the backend')
    r = requests.post(f'{B}/com.atproto.server.createSession', timeout=20,
                      json={'identifier': os.environ['BSKY_HANDLE'], 'password': os.environ['BSKY_APP_PASSWORD']})
    r.raise_for_status()
    h = {'Authorization': 'Bearer ' + r.json()['accessJwt']}
    p = {'q': q, 'limit': 100}
    if since: p['since'] = since + 'T00:00:00Z'
    if until: p['until'] = until + 'T23:59:59Z'
    posts = {}  # popular + latest posts, popular ones are likelier to have replies
    for sort in ('top', 'latest'):
        for x in requests.get(f'{B}/app.bsky.feed.searchPosts', params={**p, 'sort': sort}, headers=h, timeout=30).json().get('posts', []): posts[x['uri']] = x
    posts = list(posts.values())
    rows = {}

    def walk(n, root, parent=''):
        x = n.get('post')
        if not x: return
        rec = x['record']
        rows[x['uri']] = [x['uri'], x['author']['handle'], parent, root, rec.get('text', '').replace('\n', ' '), ts(rec.get('createdAt'))]
        for c in n.get('replies', []): walk(c, root, x['uri'])

    for x in posts: walk({'post': x}, x['uri'])
    for x in sorted([x for x in posts if x.get('replyCount')], key=lambda x: -x['replyCount'])[:threads]:
        t = requests.get(f'{B}/app.bsky.feed.getPostThread', params={'uri': x['uri'], 'depth': 6}, headers=h, timeout=30).json().get('thread', {})
        walk(t, x['uri']); time.sleep(.2)
    return rows
