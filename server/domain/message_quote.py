"""Validate a selected quote against a message's text, including rendered Markdown."""
import re


def _visible_text(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)\s]+\)", r"\1", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s+|>\s?|[-*]\s+|\d+[.)]\s+)", "", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"(?m)^\s*\|?\s*:?-{3,}.*$", "", text)
    text = re.sub(r"(?m)^.*\|.*$", lambda match: match.group().replace("|", " "), text)
    return " ".join(text.split())


def quote_content(original: str, excerpt: str | None) -> str:
    if excerpt is None:
        return original
    selected = excerpt.strip()
    if not selected or (selected not in original and " ".join(selected.split()) not in _visible_text(original)):
        raise ValueError("引用片段不属于原消息，请重新选择")
    return selected
