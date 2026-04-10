"""Tests for formatting utilities."""

from uuid import UUID

from ynab_tools.utils.formatting import format_amount, format_amount_colored, millis_to_dollars, short_id


def test_millis_to_dollars():
    assert millis_to_dollars(1000) == 1.0
    assert millis_to_dollars(-45990) == -45.99
    assert millis_to_dollars(0) == 0.0


def test_format_amount_positive():
    assert format_amount(1000) == "$1.00"
    assert format_amount(123450) == "$123.45"
    assert format_amount(0) == "$0.00"


def test_format_amount_negative():
    assert format_amount(-45990) == "-$45.99"
    assert format_amount(-1000000) == "-$1,000.00"


def test_format_amount_thousands_separator():
    assert format_amount(1234567890) == "$1,234,567.89"


def test_format_amount_colored_positive():
    result = format_amount_colored(5000)
    assert "[green]" in result
    assert "$5.00" in result


def test_format_amount_colored_negative():
    result = format_amount_colored(-5000)
    assert "[red]" in result
    assert "$5.00" in result


def test_short_id_string():
    assert short_id("abcdef12-3456-7890") == "abcdef12"


def test_short_id_uuid():
    uuid = UUID("abcdef12-3456-7890-abcd-ef1234567890")
    assert short_id(uuid) == "abcdef12"


def test_short_id_none():
    assert short_id(None) == "—"


def test_short_id_empty():
    assert short_id("") == "—"
