import re, requests, xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

def news(q, when='7d', after='', before=''):
    """Headlines from Google News RSS for any topic. Recent: when=1d|7d|1y. Past: after/before as YYYY-MM-DD."""
    qs = f'{q} after:{after} before:{before}' if after and before else f'{q} when:{when}'
    r = requests.get('https://news.google.com/rss/search', params={'q': qs, 'hl': 'en-IN', 'gl': 'IN', 'ceid': 'IN:en'},
                     headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
    r.raise_for_status()
    out, seen = [], set()
    for i in ET.fromstring(r.content).findall('.//item'):
        k = re.sub(r'\W+', '', re.sub(r' - [^-]+$', '', i.findtext('title') or '').lower())[:80]
        if k in seen or len(out) >= 40: continue
        seen.add(k)
        try: d = parsedate_to_datetime(i.findtext('pubDate')).strftime('%d %b %Y')
        except Exception: d = ''
        out.append({'title': i.findtext('title') or '', 'source': i.findtext('source') or '', 'date': d, 'link': i.findtext('link') or ''})
    return out
