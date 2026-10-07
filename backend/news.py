"""Fetches news headlines from Google News RSS to correlate media trends with discussion cascades."""
import re, requests, xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

def get_news(q: str, when: str = "7d", after: str = "", before: str = "") -> list:
    """Headlines from Google News RSS for any topic.
    Recent: when=1d|7d|1y. Past: after/before as YYYY-MM-DD.
    """
    if not q or not q.strip():
        q = "trending news"
    clean_q = q.replace("t3_", "").replace("bsky:", "").replace("masto:", "").strip()
    qs = f"{clean_q} after:{after} before:{before}" if after and before else f"{clean_q} when:{when}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.get(
            "https://news.google.com/rss/search",
            params={"q": qs, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers=headers,
            timeout=15
        )
        r.raise_for_status()
    except Exception as e:
        return []

    out, seen = [], set()
    try:
        root = ET.fromstring(r.content)
        for i in root.findall(".//item"):
            title = i.findtext("title") or ""
            norm_key = re.sub(r"\W+", "", re.sub(r" - [^-]+$", "", title).lower())[:80]
            if not norm_key or norm_key in seen or len(out) >= 30:
                continue
            seen.add(norm_key)

            pub_date_raw = i.findtext("pubDate")
            try:
                d = parsedate_to_datetime(pub_date_raw).strftime("%d %b %Y") if pub_date_raw else ""
            except Exception:
                d = ""

            source_elem = i.find("source")
            source_name = source_elem.text if source_elem is not None else ""
            if not source_name and " - " in title:
                source_name = title.rsplit(" - ", 1)[-1]

            out.append({
                "title": title,
                "source": source_name,
                "date": d,
                "link": i.findtext("link") or ""
            })
    except Exception:
        pass
    return out
