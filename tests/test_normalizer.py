from analysis.normalizer import normalize_team


def test_nba_abbreviation():
    assert normalize_team("LAL", "nba") == "Los Angeles Lakers"


def test_nba_alternate_name():
    assert normalize_team("LA Lakers", "nba") == "Los Angeles Lakers"


def test_nfl_abbreviation():
    assert normalize_team("KC", "nfl") == "Kansas City Chiefs"


def test_mlb_abbreviation():
    assert normalize_team("NYY", "mlb") == "New York Yankees"


def test_unknown_name_passthrough():
    assert normalize_team("Unknown Team", "nba") == "Unknown Team"


def test_case_sensitive_no_match():
    # Keys are exact — lowercase won't match
    assert normalize_team("lal", "nba") == "lal"
