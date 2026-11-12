"""Instagram URL rule shared by the API, the model, and public reads."""

from urllib.parse import urlparse


def clean_instagram(value):
    """'' clears the link. None means the value cannot be stored or served."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return ""
    if len(text) > 300 or any(ch.isspace() for ch in text):
        return None
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return text
