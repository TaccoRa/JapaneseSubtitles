"""Turn browser page metadata into a search title, without site chrome words."""
import re
from urllib.parse import unquote, urlparse


def browser_search_title(title, page_url=""):
    parsed = urlparse(str(page_url or ""))
    if re.fullmatch(r"(?:[\w-]+\.)*miruro\.[a-z]+", parsed.hostname or "", re.I):
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] in ("watch", "info"):
            slug = unquote(parts[2]).replace("-", " ").strip()
            if slug:
                return slug
    text = re.sub(r"^\s*watch\s+", "", str(title or ""), flags=re.I)
    text = re.sub(r"\s*(?:[-|–—]\s*)?(?:miruro|netflix|youtube|aniworld)\s*$", "", text, flags=re.I)
    text = re.sub(r"\s*(?:[-|–—]\s*)?(?:episode|folge|ep)\s*\d+.*$", "", text, flags=re.I)
    return text.strip(" -|–—")
