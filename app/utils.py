import re
from typing import Optional


def normalize_phone_number(phone: Optional[str]) -> Optional[str]:
    """
    Normalizes a phone number to standard E.164-compatible format:
    - Strips whitespace, parentheses, dashes, and dots.
    - Preserves leading '+' if present.
    - Validates that the remaining digits count is between 7 and 15 digits.
    Returns the normalized string (e.g. '+919876543210' or '15551234567')
    or None if the phone number is invalid.
    """
    if not phone:
        return None

    cleaned = phone.strip()
    if not cleaned:
        return None

    has_plus = cleaned.startswith("+")
    digits = re.sub(r"\D", "", cleaned)

    # Standard E.164 recommendations: 7 to 15 digits
    if len(digits) < 7 or len(digits) > 15:
        return None

    return f"+{digits}" if has_plus else digits
