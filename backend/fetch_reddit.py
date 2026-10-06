# Usage: python fetch_reddit.py [max_rows] [thread_id1,thread_id2,... | mine]   ('mine' = the 5 project threads)
# Needs Kaggle login first: set KAGGLE_USERNAME + KAGGLE_KEY (from kaggle.com > Settings > Create New Token)
import io, os, sys, json, glob, kagglehub, zstandard
from common import save
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
MINE = 'b7sa1t,b7tup6,b7sycp,b80i87,b7yijr'
T = {t.replace('t3_', '') for t in (MINE if sys.argv[2:3] == ['mine'] else sys.argv[2]).split(',')} if len(sys.argv) > 2 else None
path = kagglehub.dataset_download(os.getenv('REDDIT_DATASET', 'i221113hadiyatanveer/the-pushshift-reddit-dataset-submissions'))
print('downloaded to', path)

def lines(f):
    if f.endswith('.zst'):
        with open(f, 'rb') as fh:
            yield from io.TextIOWrapper(zstandard.ZstdDecompressor(max_window_size=2**31).stream_reader(fh), encoding='utf-8', errors='ignore')
    else:
        yield from open(f, encoding='utf-8', errors='ignore')

rows = []
for f in glob.glob(f'{path}/**/*', recursive=True):
    if not f.endswith(('.zst', '.json', '.jsonl', '.ndjson')): continue
    for l in lines(f):
        try: d = json.loads(l)
        except ValueError: continue
        if 'parent_id' not in d or 'body' not in d: continue  # keep [deleted]/[removed] rows: dropping them breaks parent chains
        if T and d['link_id'].replace('t3_', '') not in T: continue
        rows.append([d['id'], d.get('author', ''), d['parent_id'], d['link_id'], d['body'].replace('\n', ' '), d.get('created_utc', 0)])
        if len(rows) >= N: break
    if len(rows) >= N: break
if rows: save('reddit', rows)
else: print('No comment rows found (this dataset may hold submissions only). Use a comments dump, or copy your thread_sample.csv to data/reddit.csv')
