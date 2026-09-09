"""Normalization + validation helpers shared by the REST API and the voice agent.

Design note: speech-to-text output is messy. A caller says "March fifth, nineteen
eighty-five" and the LLM may hand us "March 5, 1985", "3/5/1985" or "1985-03-05".
These helpers are *lenient on input, strict on output* - we accept anything a human
plausibly said, but everything that reaches the database is canonical.
"""
import re
from datetime import date, datetime
from typing import Optional

# --- Sex ------------------------------------------------------------------
SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")

_SEX_ALIASES = {
    "m": "Male",
    "male": "Male",
    "man": "Male",
    "f": "Female",
    "female": "Female",
    "woman": "Female",
    "o": "Other",
    "other": "Other",
    "non-binary": "Other",
    "nonbinary": "Other",
    "x": "Other",
    "decline": "Decline to Answer",
    "decline to answer": "Decline to Answer",
    "prefer not to say": "Decline to Answer",
    "prefer not to answer": "Decline to Answer",
    "unknown": "Decline to Answer",
    "n/a": "Decline to Answer",
}

# --- States ---------------------------------------------------------------
US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico", "VI": "U.S. Virgin Islands", "GU": "Guam",
}
_STATE_NAME_TO_CODE = {name.lower(): code for code, name in US_STATES.items()}

# Names: letters plus the punctuation that legitimately appears in them
# (O'Brien, Smith-Jones, Jr.). Unicode-aware so "Jose" and "Nguyen" with
# diacritics are not rejected. Digits and underscores are never allowed.
_NAME_RE = re.compile(
    r"^[^\W\d_][^\W\d_ '\-\.]*(?:[ '\-\.][^\W\d_][^\W\d_ '\-\.]*)*$", re.UNICODE
)
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
_MEMBER_ID_RE = re.compile(r"^[A-Za-z0-9\-]{2,60}$")

_DOB_FORMATS = (
    "%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%Y/%m/%d",
    "%B %d %Y", "%b %d %Y", "%d %B %Y", "%m/%d/%y",
)

# Earliest plausible DOB. Guards against transcription noise like "1085".
_MIN_DOB = date(1900, 1, 1)


class ValidationProblem(ValueError):
    """Raised with a caller-friendly message the voice agent can read aloud verbatim."""

    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(message)


def clean_text(value: Optional[str]) -> Optional[str]:
    """Collapse whitespace and strip control characters (basic input sanitization)."""
    if value is None:
        return None
    value = re.sub(r"[\x00-\x1f\x7f]", "", str(value))
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def _looks_spelled_out(value: str) -> bool:
    """True for "D-A-V-I-S" but false for "Smith-Jones".

    The distinction matters: transcribers render a caller spelling their name as
    hyphen-separated single characters, which we must join, while a genuinely
    hyphenated surname must be preserved. The rule is that EVERY hyphen-separated
    chunk must be exactly one character.
    """
    chunks = value.split("-")
    return len(chunks) >= 2 and all(len(c) == 1 and c.isalpha() for c in chunks)


def _recase(word: str) -> str:
    """Title-case only words that carry no case information of their own.

    Naive .title() destroys "McDonald" -> "Mcdonald" and "O'Brien" -> "O'brien".
    A word the caller (or transcriber) already cased deliberately is left alone.
    """
    if not word:
        return word
    if word.isupper() or word.islower():
        return word[:1].upper() + word[1:].lower()
    return word  # mixed case already - trust it


def normalize_spoken_name(value: str) -> str:
    """Collapse spelled-out letters and fix casing before validation."""
    value = clean_text(value) or ""
    parts = []
    for token in value.split(" "):
        if _looks_spelled_out(token):
            token = "".join(token.split("-"))
        parts.append("-".join(_recase(seg) for seg in token.split("-")))
    return " ".join(parts)


def validate_name(value: str, field: str) -> str:
    value = normalize_spoken_name(value)
    if not 1 <= len(value) <= 50:
        raise ValidationProblem(
            field, f"{field.replace('_', ' ')} must be 1 to 50 characters."
        )
    if not _NAME_RE.match(value):
        raise ValidationProblem(
            field,
            f"{field.replace('_', ' ')} may only contain letters, hyphens and apostrophes.",
        )
    return value


def normalize_phone(value: str, field: str = "phone_number") -> str:
    """Return a bare 10-digit U.S. number, or raise.

    Accepts "(555) 123-4567", "+1 555 123 4567", "555.123.4567", "15551234567".
    Storing the bare digits makes duplicate detection an indexed equality match
    rather than fuzzy string comparison.
    """
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValidationProblem(
            field,
            "A U.S. phone number needs exactly 10 digits, including the area code.",
        )
    # NANP rule: neither the area code nor the exchange code may start with 0 or 1.
    if digits[0] in "01" or digits[3] in "01":
        raise ValidationProblem(
            field,
            "That is not a valid U.S. number - area and exchange codes cannot start with 0 or 1.",
        )
    return digits


def format_phone(digits: Optional[str]) -> Optional[str]:
    """Presentation form used in API responses and the dashboard."""
    if not digits or len(digits) != 10:
        return digits
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def parse_dob(value) -> date:
    """Parse a date of birth from any format a voice transcript might produce."""
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        raw = clean_text(str(value)) or ""
        raw = raw.replace(",", "").replace(".", "")
        parsed = None
        for fmt in _DOB_FORMATS:
            try:
                parsed = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValidationProblem(
                "date_of_birth",
                "I could not read that date of birth. Please give it as month, day and year.",
            )
    if parsed > date.today():
        raise ValidationProblem(
            "date_of_birth", "The date of birth cannot be in the future."
        )
    if parsed < _MIN_DOB:
        raise ValidationProblem(
            "date_of_birth",
            "That date of birth looks too far in the past - please repeat the year.",
        )
    return parsed


def format_dob(value: Optional[date]) -> Optional[str]:
    return value.strftime("%m/%d/%Y") if value else None


def normalize_sex(value: str) -> str:
    raw = (clean_text(value) or "").lower()
    if raw in _SEX_ALIASES:
        return _SEX_ALIASES[raw]
    for canonical in SEX_VALUES:
        if raw == canonical.lower():
            return canonical
    raise ValidationProblem(
        "sex", "Please answer with Male, Female, Other, or Decline to Answer."
    )


def normalize_state(value: str) -> str:
    """Accept either the 2-letter code or the spoken full name ('California')."""
    raw = clean_text(value) or ""
    if len(raw) == 2 and raw.upper() in US_STATES:
        return raw.upper()
    if raw.lower() in _STATE_NAME_TO_CODE:
        return _STATE_NAME_TO_CODE[raw.lower()]
    raise ValidationProblem("state", f"'{raw}' is not a valid U.S. state.")


def normalize_zip(value: str) -> str:
    raw = re.sub(r"\s+", "", clean_text(value) or "")
    # Voice often yields "941051234" for a ZIP+4.
    if re.fullmatch(r"\d{9}", raw):
        raw = f"{raw[:5]}-{raw[5:]}"
    if not _ZIP_RE.match(raw):
        raise ValidationProblem(
            "zip_code", "A ZIP code needs 5 digits, or 9 for ZIP plus four."
        )
    return raw


def normalize_email(value: Optional[str]) -> Optional[str]:
    raw = clean_text(value)
    if not raw:
        return None
    # Voice transcripts frequently spell it out: "jane at example dot com".
    raw = re.sub(r"\s+at\s+", "@", raw, flags=re.I)
    raw = re.sub(r"\s+dot\s+", ".", raw, flags=re.I)
    raw = raw.replace(" ", "").lower()
    if not _EMAIL_RE.match(raw):
        raise ValidationProblem("email", "That email address does not look valid.")
    return raw


def normalize_member_id(value: Optional[str]) -> Optional[str]:
    raw = clean_text(value)
    if not raw:
        return None
    raw = raw.replace(" ", "").upper()
    if not _MEMBER_ID_RE.match(raw):
        raise ValidationProblem(
            "insurance_member_id", "A member ID should be letters and numbers only."
        )
    return raw


# --------------------------------------------------------------------------
# Spoken read-back formatting
#
# The confirmation read-back is generated here rather than left to the LLM. A model
# asked to "read the phone number back clearly" will sometimes render 4155550192 as
# "four billion one hundred fifty five million...". Formatting it deterministically
# means the most important turn in the call cannot be garbled.
# --------------------------------------------------------------------------
_ORDINALS = {1: "1st", 2: "2nd", 3: "3rd", 21: "21st", 22: "22nd", 23: "23rd", 31: "31st"}


def speak_digits(value: Optional[str], groups=(3, 3, 4)) -> str:
    """"4155550192" -> "4 1 5, 5 5 5, 0 1 9 2" so TTS reads it digit by digit."""
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return ""
    if not groups:
        return " ".join(digits)
    out, idx = [], 0
    for size in groups:
        chunk = digits[idx: idx + size]
        if not chunk:
            break
        out.append(" ".join(chunk))
        idx += size
    if idx < len(digits):
        out.append(" ".join(digits[idx:]))
    return ", ".join(out)


def speak_zip(value: Optional[str]) -> str:
    if not value:
        return ""
    base, _, plus4 = value.partition("-")
    spoken = " ".join(base)
    if plus4:
        spoken += ", plus four, " + " ".join(plus4)
    return spoken


def speak_date(value: Optional[date]) -> str:
    """date(1985, 3, 5) -> "March 5th, 1985" - unambiguous for a US listener."""
    if not value:
        return ""
    day = _ORDINALS.get(value.day, f"{value.day}th")
    return f"{value.strftime('%B')} {day}, {value.year}"


def build_readback(data: dict) -> str:
    """Assemble the confirmation sentence the agent reads verbatim before saving.

    `data` uses the canonical (already validated) values.
    """
    name = f"{data['first_name']} {data['last_name']}"
    parts = [
        f"{name}, born {speak_date(data['date_of_birth'])}, {data['sex'].lower()}.",
        f"Phone number {speak_digits(data['phone_number'])}.",
    ]

    street = data["address_line_1"]
    if data.get("address_line_2"):
        street += f", {data['address_line_2']}"
    state_name = US_STATES.get(data["state"], data["state"])
    parts.append(
        f"Address {street}, {data['city']}, {state_name}, "
        f"ZIP {speak_zip(data['zip_code'])}."
    )

    if data.get("email"):
        parts.append(f"Email {data['email']}.")
    if data.get("insurance_provider"):
        member = data.get("insurance_member_id")
        parts.append(
            f"Insurance {data['insurance_provider']}"
            + (f", member ID {' '.join(member)}." if member else ".")
        )
    if data.get("emergency_contact_name"):
        contact = f"Emergency contact {data['emergency_contact_name']}"
        if data.get("emergency_contact_phone"):
            contact += f" at {speak_digits(data['emergency_contact_phone'])}"
        parts.append(contact + ".")
    if data.get("preferred_language") and data["preferred_language"] != "English":
        parts.append(f"Preferred language {data['preferred_language']}.")

    return " ".join(parts)
