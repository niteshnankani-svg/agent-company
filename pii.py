"""Detect and scrub personal identifiers in free text (emails, phone numbers, card numbers)."""
import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b")
_CARD = re.compile(r"\b(?:\d[ -]*?){13,16}\b")


def scrub_pii(text: str) -> str:
    return _CARD.sub("<CARD>", _PHONE.sub("<PHONE>", _EMAIL.sub("<EMAIL>", text)))


def contains_pii(text: str) -> bool:
    return scrub_pii(text) != text
