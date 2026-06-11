"""Tests for country normalization logic."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.utils.country_map import normalize_country, get_target_codes


def test_normalize_united_states():
    code, name = normalize_country("United States")
    assert code == "US"
    assert name == "United States"


def test_normalize_uk_variant():
    code, name = normalize_country("UK")
    assert code == "GB"


def test_normalize_iso2_direct():
    code, name = normalize_country("CA")
    assert code == "CA"
    assert name == "Canada"


def test_normalize_unknown():
    code, name = normalize_country("Neverland")
    assert code == ""


def test_get_target_codes_mixed():
    codes = get_target_codes(["United States", "Canada", "GB"])
    assert "US" in codes
    assert "CA" in codes
    assert "GB" in codes


def test_get_target_codes_empty():
    codes = get_target_codes([])
    assert codes == set()


if __name__ == "__main__":
    test_normalize_united_states()
    test_normalize_uk_variant()
    test_normalize_iso2_direct()
    test_normalize_unknown()
    test_get_target_codes_mixed()
    test_get_target_codes_empty()
    print("All country_map tests passed.")
