"""Query Planner for NodeZero (R5.1).
Normalises input, removes stopwords while preserving numbers next to nouns,
extracts hashtags, expands synonyms from synonyms.json, and builds an ordered
list of query variants.
"""
import os
import re
import json
from search_validator import normalise, STOPWORDS

SYNONYMS_FILE = os.path.join(os.path.dirname(__file__), "synonyms.json")

def load_synonyms() -> dict:
    if os.path.exists(SYNONYMS_FILE):
        try:
            with open(SYNONYMS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def extract_number_noun_pairs(text: str) -> list[str]:
    """Finds expressions like '100 runs', '50 miles', '7 meters'."""
    return re.findall(r"\b\d+\s+[a-zA-Z]+\b", text, flags=re.IGNORECASE)

def plan_query(raw_query: str) -> dict:
    """
    Builds ordered search variants:
    1. Full phrase
    2. All keywords (number-noun pairs kept together, stopwords removed)
    3. Synonym expansions (e.g. 'century odi')
    4. The 3 then 2 longest keywords
    5. Each single keyword
    6. Hashtag forms (#odi, #odicricket, compound tags)
    """
    norm = normalise(raw_query)
    if not norm:
        return {"variants": [], "keywords": [], "hashtags": [], "synonyms": []}

    # Extract explicit hashtags
    explicit_hashtags = [h.lower() for h in re.findall(r"#\w+", norm)]

    # Lowercase text without # for tokenization
    clean_text = norm.replace("#", "").strip()

    # Detect number-noun pairs (e.g., "100 runs")
    number_noun_pairs = [p.lower() for p in extract_number_noun_pairs(clean_text)]

    # Replace number-noun pairs with placeholders temporarily to tokenize properly
    temp_text = clean_text.lower()
    placeholders = {}
    for i, pair in enumerate(number_noun_pairs):
        ph = f"__pair_{i}__"
        placeholders[ph] = pair
        temp_text = temp_text.replace(pair, ph)

    # Tokenize words
    raw_tokens = re.findall(r"\b[a-zA-Z0-9_-]+\b", temp_text)
    meaningful_tokens = []
    for tok in raw_tokens:
        if tok in placeholders:
            meaningful_tokens.append(placeholders[tok])
        elif tok not in STOPWORDS and not tok.isdigit():
            meaningful_tokens.append(tok)

    # Load synonyms
    syn_map = load_synonyms()
    matched_synonyms = []
    query_lower = clean_text.lower()

    for phrase, syn_list in syn_map.items():
        if phrase in query_lower:
            for s in syn_list:
                if s not in matched_synonyms:
                    matched_synonyms.append(s)

    # Individual keyword synonyms
    for kw in meaningful_tokens:
        if kw in syn_map:
            for s in syn_map[kw]:
                if s not in matched_synonyms:
                    matched_synonyms.append(s)

    # Ordered variants list
    variants = []
    seen = set()

    def add_variant(v):
        v_clean = normalise(v).lower()
        if v_clean and v_clean not in seen:
            variants.append(v_clean)
            seen.add(v_clean)

    # 1. Full phrase
    add_variant(clean_text)

    # 2. All keywords
    if meaningful_tokens:
        all_kw_phrase = " ".join(meaningful_tokens)
        add_variant(all_kw_phrase)

    # 3. Synonym-expanded phrase (e.g. '100 runs in odi' -> 'century odi')
    if matched_synonyms and meaningful_tokens:
        # Try replacing parts with their top synonym
        for syn in matched_synonyms[:3]:
            # If the synonym is a single or multi-word replacement
            expanded_tokens = []
            replaced = False
            for tok in meaningful_tokens:
                if tok in syn_map and syn in syn_map[tok] and not replaced:
                    expanded_tokens.append(syn)
                    replaced = True
                else:
                    expanded_tokens.append(tok)
            if replaced:
                add_variant(" ".join(expanded_tokens))
            else:
                # Also add synonym + other tokens
                add_variant(f"{syn} {' '.join(meaningful_tokens[:2])}")

    # 4. The 3 then 2 longest keywords
    sorted_by_len = sorted(meaningful_tokens, key=len, reverse=True)
    if len(sorted_by_len) >= 3:
        add_variant(" ".join(sorted_by_len[:3]))
    if len(sorted_by_len) >= 2:
        add_variant(" ".join(sorted_by_len[:2]))

    # 5. Each single keyword
    for kw in sorted_by_len:
        add_variant(kw)

    # Add single top synonyms
    for syn in matched_synonyms[:3]:
        add_variant(syn)

    # 6. Hashtag forms
    hashtag_variants = []
    # Explicit hashtags from input
    for ht in explicit_hashtags:
        ht_val = ht if ht.startswith("#") else f"#{ht}"
        hashtag_variants.append(ht_val)

    # Single keywords as hashtags
    for kw in meaningful_tokens:
        clean_tag = re.sub(r"[^a-zA-Z0-9]", "", kw)
        if clean_tag and not clean_tag.isdigit() and clean_tag not in STOPWORDS:
            hashtag_variants.append(f"#{clean_tag}")

    # Compound tag (e.g., #odicricket, #100runs)
    if len(meaningful_tokens) >= 2:
        compound = "".join(re.sub(r"[^a-zA-Z0-9]", "", k) for k in meaningful_tokens[:3])
        if compound:
            hashtag_variants.append(f"#{compound}")

    # Synonym compound / tags
    for syn in matched_synonyms[:2]:
        clean_syn_tag = re.sub(r"[^a-zA-Z0-9]", "", syn)
        if clean_syn_tag:
            hashtag_variants.append(f"#{clean_syn_tag}")

    for ht in hashtag_variants:
        add_variant(ht)

    return {
        "raw": raw_query,
        "normalised": norm,
        "keywords": meaningful_tokens,
        "hashtags": explicit_hashtags,
        "synonyms": matched_synonyms,
        "variants": variants
    }
