import os, subprocess, sys
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import graph, detector, news as nw, live
from common import D

app = FastAPI(title='NodeZero')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])
def names(): return sorted(f[:-4] for f in os.listdir(D) if f.endswith('.csv') and f != 'labeled.csv')  # every CSV in data/ is a source

def ok(f, *a):
    if graph.S.g is None: raise HTTPException(400, 'Load a data source first')
    try: return f(*a)
    except KeyError: raise HTTPException(404, 'User or thread not found')

@app.get('/sources')
def sources(): return names()

@app.post('/load')
def load(source: str = 'sample'):
    if source not in names(): raise HTTPException(404, f'data/{source}.csv not found. Run its fetch script first (fetch_{source}.py)')
    return graph.load(f'{D}/{source}.csv')

@app.get('/network-graph')
def network(limit: int = 300): return ok(graph.graph_json, limit)

@app.get('/summary')
def summary(root: str = ''): return ok(graph.summary, root)

@app.get('/threads')
def threads(k: int = 20): return ok(graph.threads, k)

@app.get('/cascade')
def cascade(root: str): return ok(graph.cascade, root)

@app.post('/score-all')
def score_all(): return ok(graph.score_all)

@app.get('/top-influencers')
def inf(): return ok(graph.top, 'inf')

@app.get('/top-bot-scores')
def bots(): return ok(graph.top, 'bot')

@app.get('/user/{username}/network')
@app.get('/user/{username}')
def user(username: str): return ok(graph.user, username)

@app.get('/root/{rid:path}')
def root(rid: str): return ok(graph.root, rid)

@app.get('/trace-root/{author:path}')
def trace(author: str): return ok(graph.trace, author)

@app.get('/classify')
def classify(text: str):
    s = float(detector.score([text])[0])
    return {'score': round(s, 2), 'verdict': graph.lab(s)}

@app.get('/topic')
def topic(q: str):
    r = ok(graph.topic, q)
    if not r: raise HTTPException(404, 'No posts match that topic in this source. Try another word, or fetch fresh data for it.')
    return r

@app.get('/news')
def news(q: str, when: str = '7d', after: str = '', before: str = ''):
    try: return nw.news(q, when, after, before)
    except Exception as e: raise HTTPException(502, f'News fetch failed: {e}')

@app.post('/search-live')
def search_live(q: str, since: str = '', until: str = ''):
    r = live.search(q, since, until)
    if not r['posts']: raise HTTPException(404, 'No posts found on Bluesky, Mastodon or the Reddit data for that topic. ' + '; '.join(r['errors']))
    return r

JOBS = {}
CMD = {'mastodon': ['fetch_mastodon.py'], 'bluesky': ['fetch_bluesky.py', '1500']}
LOG = lambda s: f'{D}/fetch_{s}.log'

@app.post('/refresh')
def refresh(source: str):
    if source not in CMD: raise HTTPException(400, 'Live fetch works for mastodon and bluesky')
    p = JOBS.get(source)
    if not p or p.poll() is not None:
        with open(LOG(source), 'w') as f:
            JOBS[source] = subprocess.Popen([sys.executable, '-u', *CMD[source]], cwd=os.path.dirname(os.path.abspath(__file__)),
                                            stdout=f, stderr=subprocess.STDOUT)
    return {'running': True}

@app.get('/refresh-status')
def refresh_status(source: str):
    p = JOBS.get(source)
    log = open(LOG(source), errors='ignore').read().strip().splitlines()[-2:] if os.path.exists(LOG(source)) else []
    return {'running': bool(p and p.poll() is None), 'log': ' | '.join(log)}
