# Usage: python fetch_youtube.py <video-url-or-id> [max_comments]   -> data/youtube.csv   (needs env YOUTUBE_API_KEY)
import sys
import youtube as yt
from common import save
v = yt.video_id(sys.argv[1]) if len(sys.argv) > 1 else None
if not v: sys.exit('Give a YouTube video link or 11-character video id')
rows = yt.comments(v, int(sys.argv[2]) if len(sys.argv) > 2 else 400)
save('youtube', list(rows.values())) if rows else print('No comments found (comments may be disabled)')
