"""Turning what a user types in the trips search into a SQL LIKE pattern."""

# The LIKE metacharacters are literals in a typed term; `*` and `?` are the
# wildcards the user means by them.
_LIKE_ESCAPES = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


def has_wildcard(term: str) -> bool:
    return "*" in term or "?" in term


def like_pattern(term: str, exact: bool = False) -> str:
    """`term` as a LIKE pattern, `*` matching any run of characters and `?` one.

    A wildcard term describes the whole value rather than a fragment of it, so it is
    anchored: "from:Paris*" is "starts with Paris", not "contains Paris anywhere".
    Anything else keeps the plain substring match, unless `exact` asks for the term
    on its own.
    """
    pattern = term.translate(_LIKE_ESCAPES).replace("*", "%").replace("?", "_")
    return pattern if exact or has_wildcard(term) else f"%{pattern}%"
