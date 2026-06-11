"""
Task signatures — the retrieval key for procedural memory.

The question memory must answer is: "have I done THIS task before?" We need a
stable, normalized key so that "Create a new lead for Acme Corp" and "Create a
new lead for Globex Inc" map to the SAME procedure (create-a-lead), while
"Update an account" maps to a different one.

First version is deliberately NON-embedding: a normalized, keyword-based
signature. It's deterministic, debuggable, and free. If/when fuzzy matching is
needed, an embedding layer can sit on top WITHOUT changing the stored schema —
the signature stays as the human-readable anchor.
"""
from __future__ import annotations

import re

# Words that carry no task identity — stripped so specific values
# (company names, dates, record ids) don't fragment the signature.
_STOPWORDS = {
    "a", "an", "the", "for", "to", "of", "in", "on", "and", "or", "with",
    "new", "this", "that", "please", "then", "into", "from", "my", "our",
}

# Very common task verbs we want to preserve as the core of the signature.
_TASK_VERBS = {
    "create", "update", "delete", "add", "remove", "edit", "find", "search",
    "open", "navigate", "fill", "submit", "extract", "verify", "check",
    "select", "click", "send", "export", "import", "assign", "close", "log",
}


def _tokens(text: str) -> list[str]:
    # 1. Strip specific values BEFORE lowercasing so we can use case as a
    #    signal: urls, quoted strings, numbers/ids.
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[\"'].*?[\"']", " ", text)
    text = re.sub(r"\b\d[\w-]*\b", " ", text)      # ids, numbers

    # 2. Strip proper-noun-like value tokens: a Capitalized word that is NOT
    #    the first word of the goal and is NOT a known task verb is almost
    #    always a specific value (a company, person, record name) rather than
    #    task identity. "Create a new lead for Acme Corp" -> drop "Acme Corp".
    #    The sentence-initial word is exempted (it's usually the verb,
    #    capitalized only because it starts the sentence).
    words_raw = text.split()
    kept: list[str] = []
    for idx, w in enumerate(words_raw):
        stripped = re.sub(r"[^A-Za-z]", "", w)
        if not stripped:
            continue
        is_capitalized = stripped[0].isupper()
        if idx > 0 and is_capitalized and stripped.lower() not in _TASK_VERBS:
            continue  # treat as a specific value, drop it
        kept.append(stripped.lower())

    return [w for w in kept if w not in _STOPWORDS and len(w) > 1]


def normalize_goal(goal: str) -> str:
    """Turn a free-text goal into a normalized signature string.

    Strategy: keep task verbs and the salient nouns, drop filler and
    specific values, sort for order-independence, dedupe. The result is a
    compact canonical form two equivalent tasks will share.

    Examples:
      "Create a new lead for Acme Corp"  -> "create lead"
      "Create a new lead for Globex Inc" -> "create lead"
      "Update the account billing address" -> "account address billing update"
    """
    toks = _tokens(goal)
    if not toks:
        return ""
    verbs = [t for t in toks if t in _TASK_VERBS]
    others = [t for t in toks if t not in _TASK_VERBS]
    # Keep verbs first (task identity), then the most salient nouns sorted
    # for order-independence. Cap noun count so a long goal doesn't create
    # an over-specific signature that never matches again.
    salient = sorted(set(others))[:4]
    parts = sorted(set(verbs)) + salient
    return " ".join(parts)


def signature_for_plan(goal: str, step_descriptions: list[str]) -> str:
    """Build the task signature for a whole plan: the normalized goal, plus
    a light contribution from step descriptions to disambiguate plans that
    share a goal phrase but do materially different things.
    """
    base = normalize_goal(goal)
    # Fold in a couple of distinctive step verbs not already in the base.
    step_toks: list[str] = []
    for desc in step_descriptions:
        for t in _tokens(desc):
            if t in _TASK_VERBS:
                step_toks.append(t)
    extra = sorted(set(step_toks) - set(base.split()))[:3]
    if extra:
        return (base + " | " + " ".join(extra)).strip(" |")
    return base