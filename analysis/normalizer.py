NBA_MAP = {
    "LAL": "Los Angeles Lakers", "LA Lakers": "Los Angeles Lakers",
    "GSW": "Golden State Warriors", "GS Warriors": "Golden State Warriors",
    "BOS": "Boston Celtics",
    "MIA": "Miami Heat",
    "PHX": "Phoenix Suns",
    "DEN": "Denver Nuggets",
    "MIL": "Milwaukee Bucks",
    "PHI": "Philadelphia 76ers",
    "NYK": "New York Knicks",
    "CHI": "Chicago Bulls",
    "BKN": "Brooklyn Nets",
    "DAL": "Dallas Mavericks",
    "LAC": "Los Angeles Clippers",
    "SAC": "Sacramento Kings",
    "MEM": "Memphis Grizzlies",
    "NOP": "New Orleans Pelicans",
    "MIN": "Minnesota Timberwolves",
    "OKC": "Oklahoma City Thunder",
    "UTA": "Utah Jazz",
    "POR": "Portland Trail Blazers",
    "CLE": "Cleveland Cavaliers",
    "ATL": "Atlanta Hawks",
    "TOR": "Toronto Raptors",
    "IND": "Indiana Pacers",
    "WAS": "Washington Wizards",
    "DET": "Detroit Pistons",
    "CHA": "Charlotte Hornets",
    "ORL": "Orlando Magic",
    "SAS": "San Antonio Spurs",
    "HOU": "Houston Rockets",
}

NFL_MAP = {
    "NE": "New England Patriots", "KC": "Kansas City Chiefs",
    "SF": "San Francisco 49ers", "DAL": "Dallas Cowboys",
    "BUF": "Buffalo Bills", "PHI": "Philadelphia Eagles",
    "LAR": "Los Angeles Rams", "GB": "Green Bay Packers",
    "BAL": "Baltimore Ravens", "CIN": "Cincinnati Bengals",
    "MIA": "Miami Dolphins", "NYJ": "New York Jets",
    "NYG": "New York Giants", "WAS": "Washington Commanders",
    "CHI": "Chicago Bears", "DET": "Detroit Lions",
    "MIN": "Minnesota Vikings", "SEA": "Seattle Seahawks",
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons",
    "NO": "New Orleans Saints", "TB": "Tampa Bay Buccaneers",
    "CAR": "Carolina Panthers", "PIT": "Pittsburgh Steelers",
    "CLE": "Cleveland Browns", "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars", "TEN": "Tennessee Titans",
    "HOU": "Houston Texans", "DEN": "Denver Broncos",
    "LV": "Las Vegas Raiders", "LAC": "Los Angeles Chargers",
}

MLB_MAP = {
    "NYY": "New York Yankees", "LAD": "Los Angeles Dodgers",
    "BOS": "Boston Red Sox", "CHC": "Chicago Cubs",
    "SF": "San Francisco Giants", "ATL": "Atlanta Braves",
    "HOU": "Houston Astros", "NYM": "New York Mets",
    "PHI": "Philadelphia Phillies", "SD": "San Diego Padres",
    "STL": "St. Louis Cardinals", "MIL": "Milwaukee Brewers",
    "MIN": "Minnesota Twins", "TOR": "Toronto Blue Jays",
    "SEA": "Seattle Mariners", "TB": "Tampa Bay Rays",
    "CLE": "Cleveland Guardians", "BAL": "Baltimore Orioles",
    "TEX": "Texas Rangers", "ARI": "Arizona Diamondbacks",
    "COL": "Colorado Rockies", "CIN": "Cincinnati Reds",
    "DET": "Detroit Tigers", "CWS": "Chicago White Sox",
    "KC": "Kansas City Royals", "OAK": "Oakland Athletics",
    "LAA": "Los Angeles Angels", "MIA": "Miami Marlins",
    "PIT": "Pittsburgh Pirates", "WSH": "Washington Nationals",
}

_MAPS = {"nba": NBA_MAP, "nfl": NFL_MAP, "mlb": MLB_MAP}


def normalize_team(name: str, sport: str) -> str:
    mapping = _MAPS.get(sport.lower(), {})
    return mapping.get(name, name)
