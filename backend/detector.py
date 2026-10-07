"""Heuristic Behavioral Scoring Engine & Misinformation Detector for NodeZero.
Evaluates account automation (bot likelihood) and message credibility using quantitative heuristics.
"""
import re, math
from collections import Counter

FLAG_THRESHOLD = 60

# Sensationalist & conspiracy linguistic patterns
SENSATIONAL_PATTERNS = [
    r"\b(breaking|shocking|exposed|proof|coverup|wake up|sheeple)\b",
    r"\b(mainstream media won't tell you|they don't want you to know)\b",
    r"\b(100% confirmed|truth revealed|secret agenda|hoax|conspiracy)\b",
    r"\b(miracle cure|big pharma|deep state|false flag|rigged)\b",
]

def is_decentralized_identifier(username: str) -> bool:
    """Checks if the identifier is a decentralized identifier (DID) or federated handle."""
    u = username.lower().strip()
    return u.startswith("did:") or u.startswith("at://")

def clean_username_for_entropy(username: str, platform: str = "") -> str:
    """Strips platform-specific routing/domain info before analyzing username characters."""
    u = username.strip()
    # Strip DID prefix or return empty if pure DID
    if is_decentralized_identifier(u):
        return ""
    # Strip Mastodon @handle@domain or handle@domain
    if "@" in u:
        parts = [p for p in u.split("@") if p]
        if parts:
            u = parts[0]
    # Strip Bluesky domain suffixes (.bsky.social, etc.)
    if "." in u:
        u = u.split(".")[0]
    # Strip deleted/removed/root prefixes
    if u.startswith("deleted:") or u.startswith("root:") or u.startswith("[deleted") or u.startswith("[root"):
        return ""
    return u

def evaluate_account(username: str, posts_count: int, p95_posts: float,
                     sent_replies: int, received_replies: int, platform: str = "") -> dict:
    """
    Evaluates accounts against automated bot-like behaviors using quantitative heuristics (0-100):
    - Reply Volume (+40): Output exceeds 95th percentile baseline.
    - Username Entropy/Digits (+30): Excessive numeric characters, exempting decentralized identifiers (e.g. DIDs).
    - Network Topology Ratio (+30): Out/in reply ratio > 3 (broadcast behavior).
    """
    sig = {}

    # 1. Reply Volume (+40)
    if posts_count > p95_posts and posts_count > 1:
        sig["reply volume above 95th percentile"] = 40

    # 2. Username Entropy / Digits (+30) with platform-specific exemptions
    clean_name = clean_username_for_entropy(username, platform)
    if not is_decentralized_identifier(username) and clean_name:
        digits = sum(c.isdigit() for c in clean_name)
        digit_ratio = digits / max(len(clean_name), 1)
        if digit_ratio > 0.2:
            sig["digit-heavy username"] = 30

    # 3. Network Topology Ratio (+30)
    # Broadcast behaviour: replies out to many but receives few
    ratio = sent_replies / max(received_replies, 1)
    if ratio > 3.0 and sent_replies >= 2:
        sig["replies far more than it receives"] = 30

    score = int(sum(sig.values()))
    return {
        "bot_score": score,
        "flagged": bool(score >= FLAG_THRESHOLD),
        "signals": list(sig.keys())
    }

def score_text_misinformation(text: str) -> dict:
    """
    Heuristic credibility & misinformation scoring based on sensationalism,
    excessive capitalization, exclamation intensity, and conspiracy terminology.
    Returns probability (0.0 to 1.0) and verdict label.
    """
    if not text or len(text.strip()) == 0:
        return {"score": 0.1, "verdict": "Likely reliable", "signals": []}

    t = text.strip()
    score = 0.15
    reasons = []

    # Check sensationalist keywords
    for pattern in SENSATIONAL_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            score += 0.25
            reasons.append("sensationalist / alarmist phrasing")
            break

    # Check excessive uppercase intensity (shouting)
    letters = [c for c in t if c.isalpha()]
    if len(letters) >= 15:
        upper_ratio = sum(c.isupper() for c in letters) / len(letters)
        if upper_ratio > 0.45:
            score += 0.2
            reasons.append("high uppercase shouting ratio")

    # Excessive punctuation (e.g. "???", "!!!")
    if re.search(r"[!?]{2,}", t):
        score += 0.15
        reasons.append("excessive exclamation / question punctuation")

    score = round(min(max(score, 0.05), 0.95), 2)
    verdict = "Likely misinformation" if score >= 0.55 else "Questionable" if score >= 0.4 else "Likely reliable"

    return {
        "score": score,
        "verdict": verdict,
        "signals": reasons
    }
