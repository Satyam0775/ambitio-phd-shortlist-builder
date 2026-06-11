"""
Maps country name variants to ISO-2 codes and vice-versa.
Used for hard country filtering.
"""

COUNTRY_CODE_MAP: dict[str, str] = {
    # Full names -> ISO-2
    "united states": "US",
    "usa": "US",
    "united states of america": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "great britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "canada": "CA",
    "australia": "AU",
    "germany": "DE",
    "france": "FR",
    "netherlands": "NL",
    "the netherlands": "NL",
    "holland": "NL",
    "sweden": "SE",
    "switzerland": "CH",
    "denmark": "DK",
    "norway": "NO",
    "finland": "FI",
    "austria": "AT",
    "belgium": "BE",
    "singapore": "SG",
    "new zealand": "NZ",
    "ireland": "IE",
    "italy": "IT",
    "spain": "ES",
    "japan": "JP",
    "south korea": "KR",
    "korea": "KR",
    "china": "CN",
    "hong kong": "HK",
    "india": "IN",
    "israel": "IL",
    "saudi arabia": "SA",=
    "uae": "AE",
    "united arab emirates": "AE",
    "qatar": "QA",
    "brazil": "BR",
    "argentina": "AR",
    "portugal": "PT",
    "czech republic": "CZ",
    "poland": "PL",
    "hungary": "HU",
    "turkey": "TR",
    "russia": "RU",
    "ukraine": "UA",
    "south africa": "ZA",
    "mexico": "MX",
    "columbia": "CO",
    "colombia": "CO",
    "chile": "CL",
    "taiwan": "TW",
    "malaysia": "MY",
    "indonesia": "ID",
    "thailand": "TH",
    "philippines": "PH",
    "greece": "GR",
    "luxembourg": "LU",
    "iceland": "IS",
    "estonia": "EE",
    "latvia": "LV",
    "lithuania": "LT",
    "slovakia": "SK",
    "slovenia": "SI",
    "croatia": "HR",
    "romania": "RO",
    "bulgaria": "BG",
    "serbia": "RS",
    "pakistan": "PK",
    "bangladesh": "BD",
    "egypt": "EG",
    "nigeria": "NG",
    "kenya": "KE",
    "ethiopia": "ET",
    "ghana": "GH",
}

# ISO-2 -> canonical name
ISO2_TO_NAME: dict[str, str] = {
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "DE": "Germany",
    "FR": "France",
    "NL": "Netherlands",
    "SE": "Sweden",
    "CH": "Switzerland",
    "DK": "Denmark",
    "NO": "Norway",
    "FI": "Finland",
    "AT": "Austria",
    "BE": "Belgium",
    "SG": "Singapore",
    "NZ": "New Zealand",
    "IE": "Ireland",
    "IT": "Italy",
    "ES": "Spain",
    "JP": "Japan",
    "KR": "South Korea",
    "CN": "China",
    "HK": "Hong Kong",
    "IN": "India",
    "IL": "Israel",
    "SA": "Saudi Arabia",
    "AE": "United Arab Emirates",
    "QA": "Qatar",
    "BR": "Brazil",
    "AR": "Argentina",
    "PT": "Portugal",
    "CZ": "Czech Republic",
    "PL": "Poland",
    "HU": "Hungary",
    "TR": "Turkey",
    "RU": "Russia",
    "UA": "Ukraine",
    "ZA": "South Africa",
    "MX": "Mexico",
    "CO": "Colombia",
    "CL": "Chile",
    "TW": "Taiwan",
    "MY": "Malaysia",
    "ID": "Indonesia",
    "TH": "Thailand",
    "PH": "Philippines",
    "GR": "Greece",
}


def normalize_country(raw: str) -> tuple[str, str]:
    """
    Returns (iso2_code, canonical_name) from a free-text country string.
    Returns ("", "") if not recognized.
    """
    if not raw:
        return "", ""
    key = raw.strip().lower()
    # Direct lookup
    if key in COUNTRY_CODE_MAP:
        code = COUNTRY_CODE_MAP[key]
        return code, ISO2_TO_NAME.get(code, raw.title())
    # Already an ISO-2 code
    upper = raw.strip().upper()
    if upper in ISO2_TO_NAME:
        return upper, ISO2_TO_NAME[upper]
    # Partial match (e.g. "US" inside "US-CA")
    for k, v in COUNTRY_CODE_MAP.items():
        if k in key:
            return v, ISO2_TO_NAME.get(v, raw.title())
    return "", ""


def get_target_codes(target_countries: list[str]) -> set[str]:
    """Convert list of country name strings to set of ISO-2 codes."""
    codes: set[str] = set()
    for c in target_countries:
        code, _ = normalize_country(c)
        if code:
            codes.add(code)
        else:
            # Keep raw upper for direct ISO-2 passes
            codes.add(c.strip().upper())
    return codes
