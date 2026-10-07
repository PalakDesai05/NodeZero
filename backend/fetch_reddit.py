# Usage: python fetch_reddit.py [max_rows] [thread_id1,thread_id2,... | mine]
# Needs Kaggle credentials if not cached: set KAGGLE_USERNAME and KAGGLE_KEY (from kaggle.com > Account > Create New API Token)
import io, os, sys, json, glob, kagglehub, zstandard
import pandas as pd

MINE_STR = 'b7sa1t,b7tup6,b7sycp,b80i87,b7yijr'
MINE = {t.strip().replace('t3_', '') for t in MINE_STR.split(',')}

# Parse CLI arguments supporting:
# python fetch_reddit.py 100000 mine
# python fetch_reddit.py mine
# python fetch_reddit.py 100000
# python fetch_reddit.py 100000 b7sa1t,b7tup6
N = 100000
T = MINE

args = sys.argv[1:]
for arg in args:
    if arg.isdigit():
        N = int(arg)
    elif arg.lower() == 'mine':
        T = MINE
    else:
        T = {t.strip().replace('t3_', '') for t in arg.split(',') if t.strip()}

print(f"Target threads: {T}")
print(f"Max rows: {N}")

print("Downloading/locating Pushshift Reddit dataset...")
path = kagglehub.dataset_download(os.getenv('REDDIT_DATASET', 'i221113hadiyatanveer/the-pushshift-reddit-dataset-submissions'))
print('Downloaded / located at:', path)

def lines(f):
    if f.endswith('.zst'):
        with open(f, 'rb') as fh:
            yield from io.TextIOWrapper(zstandard.ZstdDecompressor(max_window_size=2**31).stream_reader(fh), encoding='utf-8', errors='ignore')
    else:
        yield from open(f, encoding='utf-8', errors='ignore')

rows = []
files = glob.glob(f'{path}/**/*', recursive=True)
files = [f for f in files if f.endswith(('.zst', '.json', '.jsonl', '.ndjson'))]

MAX_SCAN_LINES = int(os.getenv('MAX_SCAN_LINES', '3500000'))
line_count = 0

for f in files:
    print(f"Reading {f}...")
    for l in lines(f):
        line_count += 1
        try:
            d = json.loads(l)
        except ValueError:
            continue

        raw_root = d.get('link_id') or ('t3_' + str(d.get('id', '')))
        clean_root = raw_root.replace('t3_', '')

        if T and clean_root not in T:
            if line_count >= MAX_SCAN_LINES and len(rows) > 0:
                break
            continue

        # Keep [deleted]/[removed] rows to ensure reply chains do not break
        cid = str(d.get('id', '')).replace('t1_', '').replace('t3_', '')
        author = d.get('author') or '[deleted]'
        raw_parent = d.get('parent_id') or ''
        body = str(d.get('body', '') if 'body' in d else f"{d.get('title', '')} {d.get('selftext', '')}").replace('\n', ' ')
        created_utc = int(d.get('created_utc', 0)) if d.get('created_utc') else 0
        subreddit = d.get('subreddit') or 'unknown'

        rows.append({
            'id': cid,
            'author': author,
            'parent_id': raw_parent,
            'root_id': clean_root,
            'body': body,
            'created_utc': created_utc,
            'subreddit': subreddit
        })

        if len(rows) >= N:
            break
        if line_count >= MAX_SCAN_LINES and len(rows) > 0:
            break

    if len(rows) >= N or (line_count >= MAX_SCAN_LINES and len(rows) > 0):
        break

out_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
os.makedirs(out_dir, exist_ok=True)
out_csv = os.path.join(out_dir, 'reddit.csv')

if rows:
    cols = ['id', 'author', 'parent_id', 'root_id', 'body', 'created_utc', 'subreddit']
    df = pd.DataFrame(rows, columns=cols)
    df.to_csv(out_csv, index=False)
    print(f"Saved {len(rows)} posts across {len(set(r['root_id'] for r in rows))} threads to {out_csv}")
else:
    print('No comment rows matched.')
