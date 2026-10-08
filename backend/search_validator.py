"""Search Input Validation and Query Planning Helper for NodeZero (R1).
Implements normalization, hard-invalid checks, soft-gibberish checks, and
caption keyword extraction.
"""
import re
import unicodedata

HARD_INVALID_MESSAGE = (
    "Please enter a valid hashtag, keyword, comment or caption (e.g. #vpn or 'nepal flood'). "
    "Only Bluesky and Mastodon links are supported."
)

SOFT_GIBBERISH_TEMPLATE = (
    "No meaningful results for '{query}'. This looks like invalid input. "
    "Try a valid hashtag, keyword or caption."
)

# Common English stopwords
STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are",
    "aren't", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both",
    "but", "by", "can't", "cannot", "could", "couldn't", "did", "didn't", "do", "does", "doesn't",
    "doing", "don't", "down", "during", "each", "few", "for", "from", "further", "had", "hadn't",
    "has", "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here",
    "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i", "i'd", "i'll", "i'm",
    "i've", "if", "in", "into", "is", "isn't", "it", "it's", "its", "itself", "let's", "me", "more",
    "most", "mustn't", "my", "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or",
    "other", "ought", "our", "ours", "ourselves", "out", "over", "own", "same", "shan't", "she",
    "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such", "than", "that", "that's",
    "the", "their", "theirs", "them", "themselves", "then", "there", "there's", "these", "they",
    "they'd", "they'll", "they're", "they've", "this", "those", "through", "to", "too", "under",
    "until", "up", "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were", "weren't",
    "what", "what's", "when", "when's", "where", "where's", "which", "while", "who", "who's", "whom",
    "why", "why's", "with", "won't", "would", "wouldn't", "you", "you'd", "you'll", "you're", "you've",
    "your", "yours", "yourself", "yourselves",
    # Hindi/Hinglish stopwords
    "ke", "ka", "ki", "hai", "ko", "se", "ne"
}

KNOWN_SHORT_ACRONYMS = {
    "UK", "US", "EU", "UN", "AI", "ML", "UI", "UX", "VPN", "ODI", "IPL", "T20", "ICC", "BBC", "CNN",
    "API", "URL", "SDK", "CSS", "SQL", "DNS", "RAM", "CPU", "GPU", "SEO", "RSS", "WHO", "FBI", "CIA"
}

def normalise(text: str) -> str:
    """Trim, Unicode NFKC, and collapse consecutive whitespace."""
    if not text:
        return ""
    text_str = str(text)
    nfkc = unicodedata.normalize("NFKC", text_str)
    # Collapse whitespace
    collapsed = re.sub(r"\s+", " ", nfkc).strip()
    return collapsed

def is_supported_social_url(url: str) -> bool:
    """Checks whether the URL is a supported Bluesky or Mastodon post link."""
    u = url.lower().strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    # Bluesky post link
    if "bsky.app/profile/" in u or "bsky.social" in u:
        return True
    # Mastodon post link: https://<domain>/@<user>/<id> or /users/<user>/statuses/<id>
    if re.search(r"/@[^/]+/\d+", u) or re.search(r"/users/[^/]+/statuses/\d+", u):
        return True
    # Known mastodon instances with status ID
    if any(m in u for m in ("mastodon.social", "mastodon.online", "mstdn.social", "fosstodon.org")) and re.search(r"/\d{4,}", u):
        return True
    return False

def check_hard_invalid(text: str) -> tuple[bool, str]:
    """
    Evaluates hard-invalid criteria:
    - Empty
    - Fewer than 2 letters
    - Only punctuation or digits
    - One character repeated 4+ times
    - More than 500 characters
    - A URL that is not Bluesky/Mastodon
    Returns (is_invalid, reason_message).
    """
    norm = normalise(text)

    # 1. Empty
    if not norm:
        return True, HARD_INVALID_MESSAGE

    # 2. More than 500 chars
    if len(norm) > 500:
        return True, HARD_INVALID_MESSAGE

    # 3. URL check: If it's a URL or contains a URL, ensure it's Bluesky or Mastodon
    url_match = re.search(r"https?://[^\s]+", norm)
    if url_match:
        # If the input is primarily a URL
        if norm.startswith("http://") or norm.startswith("https://"):
            if not is_supported_social_url(norm):
                return True, HARD_INVALID_MESSAGE

    # 4. Repeated character check: 4+ times in a row anywhere in the input
    if re.search(r"(.)\1{3,}", norm):
        return True, HARD_INVALID_MESSAGE

    # 5. Letters count: fewer than 2 letters (preserves Unicode / non-English letters)
    letters = [c for c in norm if c.isalpha()]
    if len(letters) < 2:
        return True, HARD_INVALID_MESSAGE

    return False, ""

def is_soft_gibberish(text: str) -> bool:
    """
    Evaluates soft-gibberish criteria:
    A 4+ letter token with vowel ratio < 0.2 or a consonant run >= 5.
    Also handles 3-letter lowercase non-vowel gibberish like 'xyz..'.
    Never flags short acronyms (UK, ODI, VPN, AI) or non-English text.
    """
    norm = normalise(text)
    if not norm:
        return False

    # Never flag supported URLs
    if norm.startswith("http://") or norm.startswith("https://"):
        return False

    # Extract tokens
    tokens = norm.split()
    for raw_token in tokens:
        # Remove hashtag if present
        clean_token = raw_token.lstrip("#")

        # Strip surrounding punctuation
        clean_token = re.sub(r"^[^a-zA-Z0-9]+|[^a-zA-Z0-9]+$", "", clean_token)
        if not clean_token:
            continue

        # Check for non-Latin / non-English characters
        # If the token contains non-ASCII letters, do not apply English consonant/vowel rules
        if any(ord(c) > 127 for c in clean_token):
            continue

        # Check known acronyms or short uppercase acronyms
        if clean_token.upper() in KNOWN_SHORT_ACRONYMS:
            continue
        if len(clean_token) <= 4 and clean_token.isupper() and clean_token.isalpha():
            continue

        # Extract only alphabet characters for vowel/consonant check
        alpha_chars = [c.lower() for c in clean_token if c.isalpha()]
        if not alpha_chars:
            continue

        vowels = set("aeiou")
        vowel_count = sum(1 for c in alpha_chars if c in vowels)
        alpha_len = len(alpha_chars)
        vowel_ratio = vowel_count / alpha_len

        # Check consonant run
        max_cons_run = 0
        curr_cons_run = 0
        for c in alpha_chars:
            if c not in vowels:
                curr_cons_run += 1
                if curr_cons_run > max_cons_run:
                    max_cons_run = curr_cons_run
            else:
                curr_cons_run = 0

        # Rule: 4+ letter token with vowel ratio < 0.2 or a consonant run >= 5
        if alpha_len >= 4:
            if vowel_ratio < 0.2 or max_cons_run >= 5:
                return True

        # Handle 3-letter zero-vowel non-acronym like 'xyz'
        if alpha_len == 3 and vowel_count == 0 and not raw_token.isupper():
            return True

    return False

def extract_caption_keywords(text: str, max_keywords: int = 6) -> list[str]:
    """
    For long captions, extract up to 6 keywords:
    1. Hashtags first (#vpn, #flood)
    2. Capitalised names (from original text)
    3. Longest non-stopwords
    """
    norm = normalise(text)
    if not norm:
        return []

    # 1. Hashtags
    hashtags = re.findall(r"#\w+", norm)

    # 2. Capitalised names (excluding first word of sentences if common)
    words_original = re.findall(r"\b[A-Za-z0-9'-]+\b", norm)
    capitalised = []
    for idx, w in enumerate(words_original):
        if w.startswith("#") or len(w) < 2:
            continue
        if w[0].isupper() and not w.isupper() and w.lower() not in STOPWORDS:
            # Check if not already added
            if w not in capitalised:
                capitalised.append(w)

    # 3. Longest non-stopwords (preserve numbers next to nouns like '100 runs')
    # First extract noun phrases with numbers if any
    number_noun_pairs = re.findall(r"\b\d+\s+[a-zA-Z]+\b", norm)

    clean_words = [
        re.sub(r"[^a-zA-Z0-9]", "", w).lower()
        for w in words_original
    ]
    non_stopwords = [
        w for w in clean_words
        if w and w not in STOPWORDS and len(w) >= 3 and not w.isdigit()
    ]
    non_stopwords_sorted = sorted(set(non_stopwords), key=lambda x: -len(x))

    results = []
    seen = set()

    def add_keyword(k):
        k_clean = k.strip()
        k_lower = k_clean.lower()
        if k_clean and k_lower not in seen and len(results) < max_keywords:
            results.append(k_clean)
            seen.add(k_lower)

    # Add hashtags first
    for ht in hashtags:
        add_keyword(ht)

    # Add number-noun pairs if relevant
    for pair in number_noun_pairs:
        add_keyword(pair)

    # Add capitalised names
    for cap in capitalised:
        add_keyword(cap)

    # Add longest non-stopwords
    for w in non_stopwords_sorted:
        add_keyword(w)

    return results[:max_keywords]

def validate_and_plan(raw_text: str) -> dict:
    """
    Main validator entry point.
    Returns:
    {
        "valid": bool,
        "reason": str,
        "normalised": str,
        "is_url": bool,
        "is_soft_gibberish": bool,
        "is_long_caption": bool,
        "extracted_keywords": list[str],
        "searching_for": str
    }
    """
    norm = normalise(raw_text)
    is_hard_inv, reason = check_hard_invalid(norm)
    if is_hard_inv:
        return {
            "valid": False,
            "reason": reason,
            "normalised": norm,
            "is_url": False,
            "is_soft_gibberish": False,
            "is_long_caption": False,
            "extracted_keywords": [],
            "searching_for": ""
        }

    is_url = bool(norm.startswith("http://") or norm.startswith("https://"))
    soft_gib = is_soft_gibberish(norm)

    # Check long caption: > 6 words or > 40 chars and not a URL
    words = norm.split()
    is_long_caption = (len(words) > 6 or len(norm) > 40) and not is_url

    extracted = []
    searching_for = norm
    if is_long_caption:
        extracted = extract_caption_keywords(norm, max_keywords=6)
        if extracted:
            searching_for = " ".join(extracted)

    return {
        "valid": True,
        "reason": "",
        "normalised": norm,
        "is_url": is_url,
        "is_soft_gibberish": soft_gib,
        "is_long_caption": is_long_caption,
        "extracted_keywords": extracted,
        "searching_for": searching_for
    }
