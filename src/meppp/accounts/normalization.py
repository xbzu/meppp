import unicodedata


def normalize_username(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def username_identity(value: str) -> str:
    return normalize_username(value).casefold()
