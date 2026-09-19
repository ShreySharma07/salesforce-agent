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
    """Tokenize a goal/step string into identity-bearing lowercase words:
    strips URLs, quoted values, numbers/ids, non-initial capitalized words
    (treated as specific values) and stopwords."""
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

# ---------------------------------------------------------------------------
# Near-match retrieval
# ---------------------------------------------------------------------------
# Exact signature equality is the fast path, but it fragments memory: reword
# a goal ("Escalate new Acme cases" vs "Escalate any new case for Acme") and
# the signature changes, so a hard-won procedure is never found again and the
# system silently relearns it. Token overlap recovers those near-duplicates
# while staying deterministic and free — no embedding model, no network.

# Jaccard overlap above this counts as "the same task". Tuned to accept
# rewordings while rejecting genuinely different tasks that share a verb
# (e.g. "create lead" vs "create task" overlap at 0.33).
SIMILARITY_THRESHOLD = 0.6


# Quantifiers carry no task identity ("escalate new cases" vs "escalate any
# case" are the same task). Dropped when COMPARING only — signature
# generation is left untouched so already-stored signatures stay valid.
_COMPARISON_NOISE = {"any", "all", "every", "each", "some", "new"}


def _stem(token: str) -> str:
    """Crudest useful stem: fold a trailing plural 's' so case/cases match.

    Deliberately not a real stemmer — signatures are short, controlled
    vocabulary, and a wrong aggressive stem would merge distinct tasks.
    """
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def signature_tokens(signature: str) -> set[str]:
    """The comparable token set of a signature (its ' | ' parts are joined).

    Stems plurals and drops quantifier noise so two phrasings of the same
    task compare equal.
    """
    return {
        _stem(t) for t in signature.replace("|", " ").split()
        if t and t not in _COMPARISON_NOISE
    }


def signature_similarity(a: str, b: str) -> float:
    """Jaccard overlap of two signatures' tokens, 0.0-1.0.

    Symmetric and order-independent, which matches how signatures are built
    (sorted, deduped tokens).
    """
    ta, tb = signature_tokens(a), signature_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def best_signature_match(
    target: str, candidates: list[str], *, threshold: float = SIMILARITY_THRESHOLD,
) -> str | None:
    """The closest candidate signature to `target`, or None below threshold.

    Ties break toward the longer (more specific) signature so a near-match
    never silently resolves to a vaguer procedure.
    """
    best, best_score = None, 0.0
    for cand in candidates:
        score = signature_similarity(target, cand)
        if score > best_score or (score == best_score and best and len(cand) > len(best)):
            if score >= threshold:
                best, best_score = cand, score
    return best
