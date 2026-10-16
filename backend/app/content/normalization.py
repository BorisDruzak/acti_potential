"""Bounded, configurable lexical normalization; identifiers never become fuzzy."""

import re
import unicodedata


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().replace("ё", "е").split())


def number_key(value):
    return re.sub(r"[\W_]", "", normalize(value), flags=re.UNICODE)


def expand(value, synonyms):
    normalized = normalize(value)
    # Explicit phrases and identifiers have exact semantics, without expansion.
    if '"' in normalized or any(char.isdigit() for char in normalized):
        return normalized
    alternatives = [normalized]
    for group in synonyms:
        if normalized in [normalize(item) for item in group]:
            alternatives.extend(normalize(item) for item in group if normalize(item) != normalized)
    return " or ".join(alternatives[:5])


def fuzzy_allowed(value):
    terms = re.findall(r"\w+", value)
    return (
        '"' not in value
        and not any(char.isdigit() for char in value)
        and 4 <= len(value) <= 80
        and len(terms) <= 6
    )
