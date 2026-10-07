# Usage: python fetch_bluesky.py [num_replies] [keyword1,keyword2]
# Streams live Bluesky replies. Reconnects automatically if the connection drops, and always saves what it has collected
# (also when you press Ctrl+C).
import asyncio, json, sys, websockets
from common import save
N = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
K = sys.argv[2].lower().split(',') if len(sys.argv) > 2 else None
HOSTS = ['jetstream1.us-east', 'jetstream2.us-east', 'jetstream1.us-west', 'jetstream2.us-west']
rows = {}

async def stream(host):
    url = f'wss://{host}.bsky.network/subscribe?wantedCollections=app.bsky.feed.post'
    async with websockets.connect(url, ping_interval=20, ping_timeout=30) as ws:
        async for m in ws:
            e = json.loads(m); c = e.get('commit', {}); r = c.get('record', {})
            if c.get('operation') != 'create' or 'reply' not in r: continue
            text = r.get('text', '')
            if K and not any(k in text.lower() for k in K): continue
            uri = f"at://{e['did']}/app.bsky.feed.post/{c['rkey']}"
            rows[uri] = [uri, e['did'], r['reply']['parent']['uri'], r['reply']['root']['uri'], text.replace('\n', ' '), e['time_us'] // 10**6]
            if len(rows) % 200 == 0: print(len(rows), 'replies')
            if len(rows) >= N: return

async def main():
    for i in range(20):
        if len(rows) >= N: break
        try: await stream(HOSTS[i % len(HOSTS)])
        except Exception as e:
            print(f'connection dropped ({type(e).__name__}), reconnecting... have {len(rows)}')
            await asyncio.sleep(2)

try: asyncio.run(main())
except KeyboardInterrupt: print('stopped early')
save('bluesky', list(rows.values())) if rows else print('nothing collected')
