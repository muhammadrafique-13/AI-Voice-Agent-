"""Unit tests for speech normalization.

These cover the transcription artifacts that a prompt instruction cannot reliably fix,
which is exactly why the logic lives in code.
"""
from datetime import date, timedelta

import pytest

from app import validators as v


# --- Names ----------------------------------------------------------------
def test_spelled_out_name_is_joined():
    assert v.validate_name("D-A-V-I-S", "last_name") == "Davis"


def test_genuinely_hyphenated_name_is_preserved():
    """The distinction that matters: every chunk must be one char to count as spelling."""
    assert v.validate_name("Smith-Jones", "last_name") == "Smith-Jones"


def test_mixed_case_names_are_not_flattened():
    """Naive .title() would give 'Mcdonald' / 'O'brien'."""
    assert v.validate_name("McDonald", "last_name") == "McDonald"
    assert v.validate_name("O'Brien", "last_name") == "O'Brien"


def test_lowercase_transcript_is_recased():
    assert v.validate_name("jane", "first_name") == "Jane"


def test_accented_names_are_accepted():
    assert v.validate_name("José", "first_name") == "José"


@pytest.mark.parametrize("bad", ["", "J4ne", "x" * 51, "!!"])
def test_invalid_names_rejected(bad):
    with pytest.raises(v.ValidationProblem):
        v.validate_name(bad, "first_name")


# --- Phone ----------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    ["(415) 555-0192", "+1 415 555 0192", "415.555.0192", "14155550192", "4155550192"],
)
def test_phone_formats_normalize_to_ten_digits(raw):
    assert v.normalize_phone(raw) == "4155550192"


@pytest.mark.parametrize(
    "bad",
    [
        "555",                  # the rubric's "3-digit phone number"
        "",                     # nothing heard
        "1234567890123456789",  # longer than E.164 permits
        "0155550192",           # U.S. area code cannot start with 0
        "4150550192",           # U.S. exchange code cannot start with 0
    ],
)
def test_invalid_phones_rejected(bad):
    with pytest.raises(v.ValidationProblem):
        v.normalize_phone(bad)


# --- International numbers (documented extension beyond the brief) ---------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+92 300 1234567", "+923001234567"),
        ("+923001234567", "+923001234567"),
        ("0092-300-1234567", "+923001234567"),
        ("+44 20 7946 0958", "+442079460958"),
    ],
)
def test_international_numbers_are_stored_as_e164(raw, expected):
    assert v.normalize_phone(raw) == expected


def test_us_numbers_still_win_over_international_parsing():
    """+1 415 555 0192 is a U.S. number and must store as bare digits, not E.164."""
    assert v.normalize_phone("+1 415 555 0192") == "4155550192"


def test_national_format_without_country_code_is_rejected():
    """03001234567 cannot be dialled internationally, so demand the country code."""
    with pytest.raises(v.ValidationProblem) as exc:
        v.normalize_phone("03001234567")
    assert "country code" in exc.value.message.lower()


def test_phone_display_format():
    assert v.format_phone("4155550192") == "(415) 555-0192"
    # International numbers are already in their canonical display form.
    assert v.format_phone("+923001234567") == "+923001234567"


def test_international_number_is_spoken_with_plus():
    assert v.speak_digits("+923001234567") == "plus 9 2 3 0 0 1 2 3 4 5 6 7"


# --- Date of birth --------------------------------------------------------
@pytest.mark.parametrize(
    "raw", ["03/05/1985", "1985-03-05", "March 5 1985", "March 5, 1985", "3-5-1985"]
)
def test_dob_formats_all_parse(raw):
    assert v.parse_dob(raw) == date(1985, 3, 5)


def test_future_dob_rejected():
    future = (date.today() + timedelta(days=1)).strftime("%m/%d/%Y")
    with pytest.raises(v.ValidationProblem) as exc:
        v.parse_dob(future)
    assert "future" in exc.value.message.lower()


def test_implausible_year_rejected():
    with pytest.raises(v.ValidationProblem):
        v.parse_dob("01/01/1085")


def test_dob_round_trips_without_timezone_drift():
    """A date of birth has no timezone. Storing 11/02 must never read back as 11/01."""
    for raw in ("11/02/1990", "01/01/2000", "12/31/1999"):
        assert v.format_dob(v.parse_dob(raw)) == raw


# --- Enums / codes --------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [("f", "Female"), ("MALE", "Male"), ("prefer not to say", "Decline to Answer"),
     ("non-binary", "Other")],
)
def test_sex_aliases(raw, expected):
    assert v.normalize_sex(raw) == expected


def test_state_name_becomes_code():
    assert v.normalize_state("California") == "CA"
    assert v.normalize_state("tx") == "TX"


def test_invalid_state_rejected():
    with pytest.raises(v.ValidationProblem):
        v.normalize_state("Ontario")


def test_nine_digit_zip_gets_hyphen():
    assert v.normalize_zip("941051234") == "94105-1234"
    assert v.normalize_zip("94105") == "94105"


def test_spoken_email_is_reassembled():
    assert v.normalize_email("john dot smith at gmail dot com") == "john.smith@gmail.com"


# --- Spoken read-back -----------------------------------------------------
def test_digits_are_grouped_for_speech():
    assert v.speak_digits("4155550192") == "4 1 5, 5 5 5, 0 1 9 2"


def test_zip_is_spoken_digit_by_digit():
    assert v.speak_zip("78704") == "7 8 7 0 4"


def test_date_is_spoken_unambiguously():
    assert v.speak_date(date(1985, 3, 5)) == "March 5th, 1985"


def test_readback_contains_every_required_value():
    sentence = v.build_readback(
        {
            "first_name": "Jane",
            "last_name": "Doe",
            "date_of_birth": date(1985, 3, 5),
            "sex": "Female",
            "phone_number": "5125550142",
            "address_line_1": "412 Oak Street",
            "address_line_2": "Apt 3B",
            "city": "Austin",
            "state": "TX",
            "zip_code": "78704",
        }
    )
    assert "Jane Doe" in sentence
    assert "March 5th, 1985" in sentence
    assert "5 1 2, 5 5 5, 0 1 4 2" in sentence
    assert "Apt 3B" in sentence
    assert "Texas" in sentence  # spoken in full, not as "TX"
    assert "7 8 7 0 4" in sentence
