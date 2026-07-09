"""Deterministic location parsing (§4.12): raw location strings → structured places.

``parse_location`` turns one raw ``positions.location`` value (or one element of a
crawler-supplied ``locations_json`` array) into ordered places, a remote-eligibility
fence, workplace markers, and a parse quality. It is the rules layer of the
structured-locations pipeline; strings it cannot resolve (``quality`` in
``('partial', 'fail')``) are the only ones the §4.7 LLM extract pass ever sees.

§17-style boundary: pure functions only — no LLM, no network, no I/O, no clock.
Input is the RAW string; ``normalize_location`` (§4.6) folds every multi-location
separator into commas and would destroy exactly the boundaries parsed here. This
module never writes anywhere: storage lives in the ``location_values`` ledger and
the ``position_locations`` projection (state.py), keyed by GEO_PARSER_VERSION —
any behavior change here MUST bump it so the store resweeps.

The tokenizer guards are load-bearing, validated against the full live corpus
(97.0% of rows parse clean): 2-3 letter ALL-CAPS aliases match only ALL-CAPS
originals (no 'at'/'per'/'can' fake anchors), absorbed city guesses must be
place-shaped (no prose laundering), and 'Remote-first' never splits as
'Remote - <place>'.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field

GEO_PARSER_VERSION = 1

# Bump when GEO_REGION_MEMBERS changes so the store reseeds geo_region_members.
GEO_REGION_SEED_VERSION = 1


@dataclass(frozen=True)
class GeoPlace:
    """One resolved place (or fence entry): city/subdivision/country or a macro-region."""

    city: str | None = None
    subdivision: str | None = None  # US state / CA province / regional code, UPPER
    country: str | None = None  # ISO 3166-1 alpha-2, UPPER
    macro: str | None = None  # canonical macro key, e.g. "emea" (see MACRO_KEYS)
    confidence: str = "high"  # 'high' | 'medium' | 'low'
    raw: str = ""  # source fragment the tokens came from


@dataclass(frozen=True)
class LocationParse:
    """Structured reading of one raw location string."""

    places: tuple[GeoPlace, ...] = ()
    fence: tuple[GeoPlace, ...] = ()  # remote-eligibility fence (country/macro level)
    remote_marker: bool = False
    worldwide: bool = False
    quality: str = "noise"  # 'full'|'macro'|'marker'|'noise'|'partial'|'fail'
    unknown: tuple[str, ...] = field(default=(), compare=False)  # unparsed fragments


# --- folding ------------------------------------------------------------------


def _deaccent(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def _fold(s: str) -> str:
    s = _deaccent(s).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[.’'`\"!?%:]", " ", s)
    s = re.sub(r"[-–—_]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# --- gazetteer ------------------------------------------------------------------

# ISO2 → aliases; the FIRST alias is the English display name (country_name()).
_COUNTRIES: dict[str, list[str]] = {
    "US": ["United States", "USA", "U.S.", "U.S.A.", "United States of America", "America",
           "EUA", "Estados Unidos", "US"],
    "GB": ["United Kingdom", "UK", "U.K.", "Great Britain", "Britain", "England", "Scotland",
           "Wales", "Northern Ireland", "GB", "GBR", "Reino Unido", "Inglaterra"],
    "CA": ["Canada", "CAN"],
    "AF": ["Afghanistan"], "AL": ["Albania"], "DZ": ["Algeria"], "AD": ["Andorra"],
    "AO": ["Angola"], "AG": ["Antigua and Barbuda", "Antigua"], "AR": ["Argentina", "ARG"],
    "AM": ["Armenia"], "AW": ["Aruba"], "AU": ["Australia", "AUS"],
    "AT": ["Austria", "Osterreich", "Oesterreich", "AT", "AUT"], "AZ": ["Azerbaijan"],
    "BS": ["Bahamas", "The Bahamas"], "BH": ["Bahrain"], "BD": ["Bangladesh"],
    "BB": ["Barbados"], "BY": ["Belarus"], "BE": ["Belgium", "Belgique", "Belgien", "BE", "BEL"],
    "BZ": ["Belize"], "BJ": ["Benin"], "BM": ["Bermuda"], "BT": ["Bhutan"], "BO": ["Bolivia"],
    "BA": ["Bosnia and Herzegovina", "Bosnia"], "BW": ["Botswana"],
    "BR": ["Brazil", "Brasil", "BR", "BRA"], "BN": ["Brunei"], "BG": ["Bulgaria", "BG", "BGR"],
    "BF": ["Burkina Faso"], "BI": ["Burundi"], "KH": ["Cambodia"], "CM": ["Cameroon"],
    "CV": ["Cape Verde", "Cabo Verde"], "KY": ["Cayman Islands"],
    "CF": ["Central African Republic"], "TD": ["Chad"], "CL": ["Chile", "CL", "CHL"],
    "CN": ["China", "Mainland China", "PRC", "CN", "CHN"], "CO": ["Colombia", "COL"],
    "KM": ["Comoros"], "CG": ["Congo"], "CD": ["Democratic Republic of the Congo", "DRC"],
    "CR": ["Costa Rica", "CR", "CRI"], "CI": ["Ivory Coast", "Cote d'Ivoire"],
    "HR": ["Croatia", "HR", "HRV"], "CU": ["Cuba"], "CW": ["Curacao"],
    "CY": ["Cyprus", "CY", "CYP"], "CZ": ["Czech Republic", "Czechia", "CZ", "CZE"],
    "DK": ["Denmark", "Danmark", "DK", "DNK"], "DJ": ["Djibouti"], "DM": ["Dominica"],
    "DO": ["Dominican Republic"], "EC": ["Ecuador", "ECU"], "EG": ["Egypt", "EGY"],
    "SV": ["El Salvador"], "GQ": ["Equatorial Guinea"], "ER": ["Eritrea"],
    "EE": ["Estonia", "EE", "EST"], "SZ": ["Eswatini", "Swaziland"], "ET": ["Ethiopia"],
    "FO": ["Faroe Islands"], "FJ": ["Fiji"], "FI": ["Finland", "Suomi", "FI", "FIN"],
    "FR": ["France", "FR", "FRA", "Franca"], "GF": ["French Guiana"], "PF": ["French Polynesia"],
    "GM": ["Gambia"], "DE": ["Germany", "Deutschland", "DE", "DEU", "GER", "Alemanha"],
    "GH": ["Ghana"],
    "GI": ["Gibraltar"], "GR": ["Greece", "GR", "GRC"], "GL": ["Greenland"], "GD": ["Grenada"],
    "GP": ["Guadeloupe"], "GU": ["Guam"], "GT": ["Guatemala"], "GG": ["Guernsey"],
    "GN": ["Guinea"], "GW": ["Guinea-Bissau"], "GY": ["Guyana"], "HT": ["Haiti"],
    "HN": ["Honduras"], "HK": ["Hong Kong", "Hong Kong SAR", "HK", "HKG"],
    "HU": ["Hungary", "HU", "HUN"], "IS": ["Iceland", "ISL"],
    "IN": ["India", "IND"], "ID": ["Indonesia", "IDN"], "IR": ["Iran"], "IQ": ["Iraq"],
    "IE": ["Ireland", "Republic of Ireland", "IE", "IRL", "Irlanda"], "IM": ["Isle of Man"],
    "IL": ["Israel", "ISR"], "IT": ["Italy", "Italia", "IT", "ITA"], "JM": ["Jamaica"],
    "JP": ["Japan", "JP", "JPN"], "JE": ["Jersey"], "JO": ["Jordan"],
    "KZ": ["Kazakhstan", "KAZ"], "KE": ["Kenya", "KEN"], "KI": ["Kiribati"], "XK": ["Kosovo"],
    "KW": ["Kuwait"], "KG": ["Kyrgyzstan"], "LA": ["Laos"], "LV": ["Latvia", "LV", "LVA"],
    "LB": ["Lebanon"], "LS": ["Lesotho"], "LR": ["Liberia"], "LY": ["Libya"],
    "LI": ["Liechtenstein"], "LT": ["Lithuania", "LT", "LTU"], "LU": ["Luxembourg", "LU", "LUX"],
    "MO": ["Macau", "Macao"], "MG": ["Madagascar"], "MW": ["Malawi"],
    "MY": ["Malaysia", "MY", "MYS"], "MV": ["Maldives"], "ML": ["Mali"], "MT": ["Malta", "MLT"],
    "MH": ["Marshall Islands"], "MQ": ["Martinique"], "MR": ["Mauritania"], "MU": ["Mauritius"],
    "MX": ["Mexico", "Mejico", "MX", "MEX"], "FM": ["Micronesia"], "MD": ["Moldova"],
    "MC": ["Monaco"], "MN": ["Mongolia"], "ME": ["Montenegro"], "MA": ["Morocco", "MAR"],
    "MZ": ["Mozambique"], "MM": ["Myanmar", "Burma"], "NA": ["Namibia"], "NR": ["Nauru"],
    "NP": ["Nepal"],
    "NL": ["Netherlands", "The Netherlands", "Holland", "Nederland", "NL", "NLD"],
    "NC": ["New Caledonia"], "NZ": ["New Zealand", "NZ", "NZL", "Aotearoa"],
    "NI": ["Nicaragua"], "NE": ["Niger"], "NG": ["Nigeria", "NGA"],
    "MK": ["North Macedonia", "Macedonia"], "NO": ["Norway", "Norge", "NO", "NOR"],
    "OM": ["Oman"], "PK": ["Pakistan", "PAK"], "PW": ["Palau"], "PS": ["Palestine"],
    "PA": ["Panama"], "PG": ["Papua New Guinea"], "PY": ["Paraguay", "PRY"],
    "PE": ["Peru", "PER"], "PH": ["Philippines", "The Philippines", "PH", "PHL"],
    "PL": ["Poland", "Polska", "PL", "POL", "Polonia"], "PT": ["Portugal", "PT", "PRT"],
    "PR": ["Puerto Rico"], "QA": ["Qatar", "QA", "QAT"], "RE": ["Reunion"],
    "RO": ["Romania", "RO", "ROU"], "RU": ["Russia", "Russian Federation", "RU", "RUS"],
    "RW": ["Rwanda"], "KN": ["Saint Kitts and Nevis"], "LC": ["Saint Lucia"],
    "VC": ["Saint Vincent and the Grenadines"], "WS": ["Samoa"], "SM": ["San Marino"],
    "ST": ["Sao Tome and Principe"], "SA": ["Saudi Arabia", "KSA", "SAU"],
    "SN": ["Senegal"], "RS": ["Serbia", "RS", "SRB"], "SC": ["Seychelles"],
    "SL": ["Sierra Leone"], "SG": ["Singapore", "SG", "SGP"], "SX": ["Sint Maarten"],
    "SK": ["Slovakia", "Slovak Republic", "SVK"], "SI": ["Slovenia", "SI", "SVN"],
    "SB": ["Solomon Islands"], "SO": ["Somalia"], "ZA": ["South Africa", "ZA", "ZAF", "RSA"],
    "KR": ["South Korea", "Korea", "Republic of Korea", "KR", "KOR"], "KP": ["North Korea"],
    "SS": ["South Sudan"], "ES": ["Spain", "Espana", "ES", "ESP", "Espanha"],
    "LK": ["Sri Lanka", "LKA"],
    "SD": ["Sudan"], "SR": ["Suriname"], "SE": ["Sweden", "Sverige", "SE", "SWE", "Suecia"],
    "CH": ["Switzerland", "Schweiz", "Suisse", "Svizzera", "CH", "CHE", "Suica"],
    "SY": ["Syria"],
    "TW": ["Taiwan", "TW", "TWN"], "TJ": ["Tajikistan"], "TZ": ["Tanzania"],
    "TH": ["Thailand", "TH", "THA"], "TL": ["Timor-Leste", "East Timor"], "TG": ["Togo"],
    "TO": ["Tonga"], "TT": ["Trinidad and Tobago", "Trinidad"], "TN": ["Tunisia", "TUN"],
    "TR": ["Turkey", "Turkiye", "TR", "TUR"], "TM": ["Turkmenistan"],
    "TC": ["Turks and Caicos"], "TV": ["Tuvalu"], "UG": ["Uganda"],
    "UA": ["Ukraine", "UA", "UKR"], "AE": ["United Arab Emirates", "UAE", "AE", "ARE",
                                           "Emirates"],
    "UY": ["Uruguay", "URY"], "UZ": ["Uzbekistan", "UZB"], "VU": ["Vanuatu"],
    "VE": ["Venezuela", "VEN"], "VN": ["Vietnam", "Viet Nam", "VN", "VNM"],
    "VG": ["British Virgin Islands"], "VI": ["US Virgin Islands"], "YE": ["Yemen"],
    "ZM": ["Zambia"], "ZW": ["Zimbabwe"], "GE": ["Georgia"],
}

COUNTRY_CODES: frozenset[str] = frozenset(_COUNTRIES)

_US_STATES: dict[str, str] = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI",
    "Wyoming": "WY", "District of Columbia": "DC", "Washington DC": "DC",
    "Washington D.C.": "DC", "D.C.": "DC", "Washington, D.C.": "DC",
}
_US_STATE_CODES = frozenset(_US_STATES.values())

_CA_PROVINCES: dict[str, str] = {
    "Ontario": "ON", "Quebec": "QC", "British Columbia": "BC", "Alberta": "AB",
    "Manitoba": "MB", "Saskatchewan": "SK", "Nova Scotia": "NS", "New Brunswick": "NB",
    "Newfoundland and Labrador": "NL", "Newfoundland": "NL", "Prince Edward Island": "PE",
    "Yukon": "YT", "Northwest Territories": "NT", "Nunavut": "NU",
}
_CA_PROV_CODES = frozenset(_CA_PROVINCES.values())

# Brazilian state codes — several (MS, SC, MA, PA, MT, AL…) collide with US state
# codes; Brazilian context in the same string flips them (see _tokenize).
_BR_STATE_CODES = frozenset({
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
})

# Macro-region alias → canonical key. Keys are the vocabulary of MACRO_KEYS,
# GeoPlace.macro, position_locations.macro_region, and GEO_REGION_MEMBERS.
_MACRO_ALIASES: dict[str, str] = {
    "EMEA": "emea",
    "Europe": "europe", "Continental Europe": "europe",
    "EU": "eu", "European Union": "eu",
    "Western Europe": "western europe", "Eastern Europe": "eastern europe",
    "Southern Europe": "southern europe", "Northern Europe": "northern europe",
    "Central Europe": "central europe",
    "Iberia": "iberia",
    "Nordics": "nordics", "Scandinavia": "scandinavia",
    "Benelux": "benelux", "DACH": "dach", "Balkans": "balkans", "Baltics": "baltics",
    "CEE": "cee",
    "APAC": "apac", "Asia Pacific": "apac", "Asia-Pacific": "apac",
    "Asia": "asia", "Southeast Asia": "southeast asia", "South Asia": "south asia",
    "East Asia": "east asia",
    "ANZ": "anz", "Oceania": "anz", "Australasia": "anz",
    "LATAM": "latam", "Latin America": "latam",
    "South America": "south america", "Central America": "central america",
    "Caribbean": "caribbean",
    "North America": "north america", "Northern America": "north america",
    "NORAM": "north america",
    "Americas": "americas", "The Americas": "americas", "AMER": "americas",
    "AMERS": "americas",
    "Africa": "africa", "Middle East": "middle east", "MENA": "mena", "GCC": "gcc",
    "Levant": "levant", "Sub-Saharan Africa": "sub saharan africa",
    "UK&I": "uki", "UKI": "uki", "UK & Ireland": "uki", "UK and Ireland": "uki",
}

_MACRO_LABELS: dict[str, str] = {
    "emea": "EMEA", "europe": "Europe", "eu": "EU",
    "western europe": "Western Europe", "eastern europe": "Eastern Europe",
    "southern europe": "Southern Europe", "northern europe": "Northern Europe",
    "central europe": "Central Europe",
    "iberia": "Iberia", "nordics": "Nordics", "scandinavia": "Scandinavia",
    "benelux": "Benelux", "dach": "DACH", "balkans": "Balkans", "baltics": "Baltics",
    "cee": "CEE",
    "apac": "APAC", "asia": "Asia", "southeast asia": "Southeast Asia",
    "south asia": "South Asia", "east asia": "East Asia",
    "anz": "ANZ", "latam": "LATAM",
    "south america": "South America", "central america": "Central America",
    "caribbean": "Caribbean", "north america": "North America", "americas": "Americas",
    "africa": "Africa", "middle east": "Middle East", "mena": "MENA", "gcc": "GCC",
    "levant": "Levant", "sub saharan africa": "Sub-Saharan Africa", "uki": "UK & Ireland",
    "worldwide": "Worldwide",
}

MACRO_KEYS: frozenset[str] = frozenset(_MACRO_LABELS)

_WORLD = ["Worldwide", "Global", "Anywhere", "Anywhere in the World", "International",
          "Everywhere", "Work from anywhere", "Globally", "World", "Anywhere in world"]

_US_REGIONS = ["US East", "US West", "US Central", "East Coast", "West Coast", "Midwest",
               "Pacific Northwest", "Northeast", "US East Coast", "US West Coast",
               "Silicon Valley", "NorCal", "SoCal", "New England", "Bay Area", "SF Bay Area",
               "San Francisco Bay Area", "Pacific Time", "Eastern Time", "Central Time",
               "Mountain Time", "USA East Coast", "USA West Coast"]

_REMOTE_MARKERS = ["remote", "remoto", "remota", "fully remote", "100 remote", "remote 100",
                   "remote first", "remote friendly", "remote ok", "remote only", "all remote",
                   "work from home", "wfh", "home based", "home office", "homeoffice",
                   "distributed", "telecommute", "teletrabajo", "virtual", "remote position",
                   "remote work", "remote role", "remote opportunity", "remote job",
                   "remote eligible", "remotely"]

_WORKPLACE_MARKERS = ["hybrid", "hibrido", "hybride", "onsite", "on site", "in office",
                      "office based", "in person", "flexible", "flex", "onside", "presencial",
                      "office first"]

_NOISE = ["hq", "headquarters", "headquarter", "office", "offices", "all offices",
          "any office", "main office", "our offices", "multiple locations",
          "various locations", "multiple", "various", "other", "others", "n a", "none",
          "tbd", "open", "location", "locations", "any location", "ubicaciones",
          "localidades", "city", "opportunity", "only", "preferred", "or", "and", "the",
          "of", "in", "based", "based in", "greater", "area", "metro area",
          "metropolitan area", "metro", "region", "any", "all", "locatie", "standort",
          "sede", "oficina", "job", "role", "position", "team", "not specified",
          "unspecified", "no location", "tba", "within", "am", "de", "del", "da", "dos",
          "das", "la", "y", "e", "und", "oder", "og", "ou", "do"]

# Famous-city → ISO2. Empirically decisive: without it, clean coverage drops
# 97.0% → 81.5% of rows and the LLM tail grows 6x. Curated against the live
# corpus; a full cities database is deliberately NOT wanted (flat tail).
_CITIES: dict[str, list[str]] = {
    "US": ["New York", "New York City", "NYC", "San Francisco", "SF", "SFO", "Los Angeles",
           "San Diego", "San Jose", "Sacramento", "Oakland", "Palo Alto", "Mountain View",
           "Menlo Park", "Sunnyvale", "Santa Clara", "Redwood City", "Foster City",
           "Cupertino", "Berkeley", "Irvine", "Santa Monica", "Long Beach", "Fresno",
           "Seattle", "Bellevue", "Redmond", "Portland", "Chicago", "Boston", "Cambridge",
           "Austin", "ATX", "Dallas", "Houston", "San Antonio", "Denver", "Boulder", "Miami",
           "Orlando", "Tampa", "Jacksonville", "Atlanta", "Nashville", "Memphis",
           "Charlotte", "Raleigh", "Durham", "Baltimore", "Philadelphia", "Pittsburgh",
           "Phoenix", "Scottsdale", "Tucson", "Tempe", "Las Vegas", "Reno",
           "Salt Lake City", "Minneapolis", "St. Paul", "Detroit", "Ann Arbor", "Columbus",
           "Cincinnati", "Cleveland", "Indianapolis", "St. Louis", "Kansas City", "Omaha",
           "Milwaukee", "Madison", "New Orleans", "Oklahoma City", "Tulsa", "Louisville",
           "Lexington", "Richmond", "Virginia Beach", "Arlington", "Alexandria", "Buffalo",
           "Rochester", "Albany", "Brooklyn", "Queens", "Manhattan", "Jersey City",
           "Newark", "Hartford", "Stamford", "Providence", "New Haven", "Des Moines",
           "Boise", "Spokane", "Anchorage", "Honolulu", "Fort Worth", "El Paso",
           "Albuquerque", "Colorado Springs", "Fort Lauderdale", "West Palm Beach",
           "Sarasota", "Savannah", "Chattanooga", "Knoxville", "Birmingham", "Huntsville",
           "Little Rock", "Wichita", "Lincoln", "Fargo", "Burlington", "Plano", "Frisco",
           "Bentonville", "Boca Raton", "Pasadena", "Burbank", "Culver City", "Emeryville",
           "South San Francisco", "San Mateo", "Walnut Creek", "Bethesda", "Reston",
           "McLean", "Tysons", "Herndon", "Santa Barbara", "Salem", "Eugene", "Tacoma",
           "Winston-Salem", "Youngstown", "Sterling Heights", "Trenton", "Vicksburg",
           "Taunton", "Silver Spring"],
    "CA": ["Toronto", "Vancouver", "Montreal", "Ottawa", "Calgary", "Edmonton", "Waterloo",
           "Kitchener", "Mississauga", "Winnipeg", "Halifax", "Victoria", "Quebec City",
           "Saskatoon", "Regina", "Hamilton", "Markham", "Burnaby", "Kelowna", "St. John's"],
    "GB": ["London", "Manchester", "Leeds", "Glasgow", "Edinburgh", "Bristol", "Liverpool",
           "Oxford", "Belfast", "Cardiff", "Nottingham", "Sheffield", "Newcastle",
           "Brighton", "Reading", "Milton Keynes", "Aberdeen", "Cheltenham", "Guildford",
           "Farnborough"],
    "IE": ["Dublin", "Cork", "Galway", "Limerick"],
    "FR": ["Paris", "Lyon", "Marseille", "Toulouse", "Bordeaux", "Nantes", "Lille", "Nice",
           "Strasbourg", "Grenoble", "Rennes", "Montpellier", "Sophia Antipolis",
           "Villeurbanne"],
    "DE": ["Berlin", "Munich", "Muenchen", "Hamburg", "Frankfurt", "Cologne", "Koln",
           "Dusseldorf", "Stuttgart", "Leipzig", "Dresden", "Nuremberg", "Hannover", "Bonn",
           "Karlsruhe", "Heidelberg", "Mannheim", "Bremen", "Essen", "Dortmund", "Walldorf",
           "Potsdam", "Aachen", "Darmstadt", "Sankt Augustin", "Heilbronn", "Taufkirchen",
           "Neckarsulm"],
    "NL": ["Amsterdam", "Rotterdam", "The Hague", "Utrecht", "Eindhoven", "Delft",
           "Den Haag", "Groningen", "Haarlem", "Leiden"],
    "BE": ["Brussels", "Antwerp", "Ghent", "Leuven", "Bruges"],
    "CH": ["Zurich", "Geneva", "Basel", "Bern", "Lausanne", "Zug", "Lugano", "Winterthur"],
    "AT": ["Vienna", "Wien", "Graz", "Linz", "Salzburg", "Innsbruck"],
    "ES": ["Madrid", "Barcelona", "Valencia", "Seville", "Sevilla", "Bilbao", "Malaga",
           "Zaragoza", "Alicante", "Granada", "Murcia", "Palma", "Las Palmas", "Vigo"],
    "PT": ["Lisbon", "Lisboa", "Porto", "Braga", "Coimbra", "Aveiro", "Faro"],
    "IT": ["Milan", "Milano", "Rome", "Roma", "Turin", "Torino", "Bologna", "Florence",
           "Firenze", "Naples", "Napoli", "Venice", "Genoa", "Padua", "Verona"],
    "PL": ["Warsaw", "Warszawa", "Krakow", "Cracow", "Wroclaw", "Gdansk", "Poznan", "Lodz",
           "Katowice", "Szczecin", "Lublin", "Bialystok", "Bydgoszcz", "Rzeszow"],
    "CZ": ["Prague", "Praha", "Brno", "Ostrava"],
    "SK": ["Bratislava", "Kosice"],
    "HU": ["Budapest", "Debrecen"],
    "RO": ["Bucharest", "Bucuresti", "Cluj", "Cluj-Napoca", "Timisoara", "Iasi", "Brasov"],
    "BG": ["Sofia", "Plovdiv", "Varna"],
    "GR": ["Athens", "Thessaloniki"],
    "HR": ["Zagreb", "Split"],
    "RS": ["Belgrade", "Novi Sad", "Nis"],
    "SI": ["Ljubljana", "Maribor"],
    "UA": ["Kyiv", "Kiev", "Kharkiv", "Lviv", "Odesa", "Odessa", "Dnipro"],
    "RU": ["Moscow", "Saint Petersburg", "Novosibirsk"],
    "EE": ["Tallinn", "Tartu"], "LV": ["Riga"], "LT": ["Vilnius", "Kaunas"],
    "FI": ["Helsinki", "Espoo", "Tampere", "Oulu", "Turku"],
    "SE": ["Stockholm", "Gothenburg", "Goteborg", "Malmo", "Uppsala", "Lund"],
    "NO": ["Oslo", "Bergen", "Trondheim", "Stavanger"],
    "DK": ["Copenhagen", "Kobenhavn", "Aarhus", "Odense", "Aalborg"],
    "IS": ["Reykjavik"], "LU": ["Luxembourg City"],
    "TR": ["Istanbul", "Ankara", "Izmir"],
    "IL": ["Tel Aviv", "Jerusalem", "Haifa", "Herzliya", "Ramat Gan", "Netanya"],
    "AE": ["Dubai", "Abu Dhabi", "Sharjah"],
    "SA": ["Riyadh", "Jeddah", "Dammam"],
    "QA": ["Doha"], "KW": ["Kuwait City"], "BH": ["Manama"], "OM": ["Muscat"],
    "JO": ["Amman"], "LB": ["Beirut"], "EG": ["Cairo", "Alexandria", "Giza"],
    "NG": ["Lagos", "Abuja"], "KE": ["Nairobi", "Mombasa"], "GH": ["Accra"],
    "ZA": ["Cape Town", "Johannesburg", "Durban", "Pretoria", "Stellenbosch"],
    "MA": ["Casablanca", "Rabat", "Marrakech"], "TN": ["Tunis"],
    "SN": ["Dakar"], "ET": ["Addis Ababa"], "UG": ["Kampala"], "RW": ["Kigali"],
    "MU": ["Ebene", "Port Louis"],
    "IN": ["Bangalore", "Bengaluru", "Mumbai", "Bombay", "Delhi", "New Delhi", "Gurgaon",
           "Gurugram", "Noida", "Hyderabad", "Chennai", "Pune", "Kolkata", "Ahmedabad",
           "Jaipur", "Kochi", "Cochin", "Thiruvananthapuram", "Trivandrum", "Indore",
           "Chandigarh", "Coimbatore", "Mysore", "Mysuru", "Nagpur", "Lucknow", "Surat",
           "Vadodara", "Bhubaneswar"],
    "PK": ["Karachi", "Lahore", "Islamabad"],
    "BD": ["Dhaka"], "LK": ["Colombo"], "NP": ["Kathmandu"],
    "CN": ["Beijing", "Shanghai", "Shenzhen", "Guangzhou", "Hangzhou", "Chengdu", "Nanjing",
           "Wuhan", "Suzhou", "Tianjin"],
    "JP": ["Tokyo", "Osaka", "Kyoto", "Yokohama", "Nagoya", "Fukuoka", "Sapporo"],
    "KR": ["Seoul", "Busan", "Incheon", "Pangyo"],
    "TW": ["Taipei", "Taipei City", "Hsinchu", "Taichung", "Kaohsiung"],
    "HK": ["Kowloon"],
    "TH": ["Bangkok", "Chiang Mai", "Phuket"],
    "VN": ["Ho Chi Minh City", "Hanoi", "Ho Chi Minh", "Saigon", "Da Nang"],
    "MY": ["Kuala Lumpur", "Penang", "Johor Bahru", "Cyberjaya"],
    "ID": ["Jakarta", "Bandung", "Surabaya", "Bali", "Yogyakarta"],
    "PH": ["Manila", "Makati", "Cebu", "Quezon City", "Taguig", "Davao"],
    "KH": ["Phnom Penh"], "MM": ["Yangon"],
    "AU": ["Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide", "Canberra", "Gold Coast",
           "Hobart", "Rockhampton"],
    "NZ": ["Auckland", "Wellington", "Christchurch"],
    "MX": ["Mexico City", "CDMX", "Ciudad de Mexico", "Guadalajara", "Monterrey", "Tijuana",
           "Queretaro", "Merida", "Puebla", "Veracruz"],
    "BR": ["Sao Paulo", "Rio de Janeiro", "Belo Horizonte", "Brasilia", "Curitiba",
           "Porto Alegre", "Florianopolis", "Recife", "Salvador", "Fortaleza", "Campinas",
           "Goiania", "Manaus", "Belem", "Vitoria", "Natal", "Joinville", "Blumenau",
           "Sao Jose dos Campos", "Sao Leopoldo", "Uberlandia", "Maringa", "Londrina"],
    "AR": ["Buenos Aires", "Cordoba", "Rosario", "Mendoza", "La Plata"],
    "CL": ["Santiago", "Valparaiso", "Concepcion"],
    "CO": ["Bogota", "Medellin", "Cali", "Barranquilla", "Cartagena"],
    "PE": ["Lima", "Arequipa", "Cusco"],
    "UY": ["Montevideo"], "EC": ["Quito", "Guayaquil"], "BO": ["La Paz", "Santa Cruz"],
    "PY": ["Asuncion"], "VE": ["Caracas"], "PA": ["Panama City"],
    "GT": ["Guatemala City"], "DO": ["Santo Domingo"], "KZ": ["Almaty", "Astana"],
    "GE": ["Tbilisi"], "AM": ["Yerevan"], "AZ": ["Baku"], "BY": ["Minsk"],
    "MD": ["Chisinau"], "CY": ["Nicosia", "Limassol"], "MT": ["Valletta", "Sliema"],
    "JM": ["Kingston", "Montego Bay"], "BS": ["Nassau"],
}


def country_name(iso2: str | None) -> str | None:
    """English display name for an ISO2 code ('US' → 'United States')."""
    if not iso2:
        return None
    aliases = _COUNTRIES.get(iso2)
    return aliases[0] if aliases else None


def macro_label(key: str | None) -> str | None:
    """Human label for a canonical macro key ('emea' → 'EMEA')."""
    if not key:
        return None
    return _MACRO_LABELS.get(key)


def place_display(place: GeoPlace) -> str:
    """Canonical human form with the full English country name (search bridge)."""
    if place.macro:
        return macro_label(place.macro) or place.macro
    parts: list[str] = []
    if place.city:
        parts.append(place.city)
    if place.subdivision:
        parts.append(place.subdivision)
    name = country_name(place.country)
    if name:
        parts.append(name)
    return ", ".join(parts) if parts else (place.raw or "")


# --- macro-region membership (remote_in= filter expansion) ----------------------

_EU = ("AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
       "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE")
_EUROPE = _EU + ("GB", "CH", "NO", "IS", "LI", "UA", "MD", "RS", "BA", "ME", "MK", "AL",
                 "XK", "TR", "GE", "AM", "AZ")
_MENA = ("AE", "BH", "DZ", "EG", "IL", "IQ", "IR", "JO", "KW", "LB", "LY", "MA", "OM",
         "PS", "QA", "SA", "SY", "TN", "YE")
_AFRICA = ("DZ", "AO", "BJ", "BW", "BF", "BI", "CV", "CM", "CF", "TD", "KM", "CG", "CD",
           "CI", "DJ", "EG", "GQ", "ER", "SZ", "ET", "GM", "GH", "GN", "GW", "KE", "LS",
           "LR", "LY", "MG", "MW", "ML", "MR", "MU", "MA", "MZ", "NA", "NE", "NG", "RW",
           "SN", "SC", "SL", "SO", "ZA", "SS", "SD", "TG", "TN", "TZ", "UG", "ZM", "ZW")
_SOUTH_AMERICA = ("AR", "BO", "BR", "CL", "CO", "EC", "GY", "PE", "PY", "SR", "UY", "VE")
_CENTRAL_AMERICA = ("BZ", "CR", "GT", "HN", "NI", "PA", "SV")
_CARIBBEAN = ("AG", "BB", "BS", "CU", "CW", "DM", "DO", "GD", "HT", "JM", "KN", "KY",
              "LC", "PR", "TT", "VC", "VG", "VI")
_NORTH_AMERICA = ("US", "CA", "MX")

# Approximate by design: these power filter *recall* (macro-expanded remote_in=),
# never eligibility truth. Well-bounded groups are exact; continental ones are
# the pragmatic job-market reading.
GEO_REGION_MEMBERS: dict[str, tuple[str, ...]] = {
    "eu": _EU,
    "europe": _EUROPE,
    "emea": tuple(dict.fromkeys(_EUROPE + _MENA + _AFRICA)),
    "western europe": ("FR", "DE", "NL", "BE", "LU", "CH", "AT", "GB", "IE"),
    "eastern europe": ("BG", "BY", "CZ", "HU", "MD", "PL", "RO", "RU", "SK", "UA"),
    "southern europe": ("ES", "PT", "IT", "GR", "HR", "SI", "MT", "CY"),
    "northern europe": ("DK", "EE", "FI", "IS", "LV", "LT", "NO", "SE", "GB", "IE"),
    "central europe": ("AT", "CH", "CZ", "DE", "HU", "PL", "SK", "SI"),
    "iberia": ("ES", "PT"),
    "nordics": ("DK", "FI", "IS", "NO", "SE"),
    "scandinavia": ("DK", "NO", "SE"),
    "benelux": ("BE", "NL", "LU"),
    "dach": ("DE", "AT", "CH"),
    "balkans": ("AL", "BA", "BG", "GR", "HR", "ME", "MK", "RO", "RS", "SI", "XK"),
    "baltics": ("EE", "LV", "LT"),
    "cee": ("AL", "BA", "BG", "CZ", "EE", "HR", "HU", "LT", "LV", "MD", "ME", "MK", "PL",
            "RO", "RS", "SI", "SK", "UA", "XK"),
    "uki": ("GB", "IE"),
    "apac": ("AU", "BD", "BN", "CN", "FJ", "HK", "ID", "IN", "JP", "KH", "KR", "LA", "LK",
             "MM", "MN", "MY", "NP", "NZ", "PG", "PH", "PK", "SG", "TH", "TW", "VN"),
    "asia": ("BD", "BN", "CN", "HK", "ID", "IN", "JP", "KG", "KH", "KR", "KZ", "LA", "LK",
             "MM", "MN", "MY", "NP", "PH", "PK", "SG", "TH", "TJ", "TM", "TW", "UZ", "VN"),
    "southeast asia": ("BN", "ID", "KH", "LA", "MM", "MY", "PH", "SG", "TH", "TL", "VN"),
    "south asia": ("AF", "BD", "BT", "IN", "LK", "MV", "NP", "PK"),
    "east asia": ("CN", "HK", "JP", "KP", "KR", "MO", "MN", "TW"),
    "anz": ("AU", "NZ"),
    "latam": tuple(dict.fromkeys(("MX",) + _CENTRAL_AMERICA + _SOUTH_AMERICA +
                                 ("CU", "DO", "PR"))),
    "south america": _SOUTH_AMERICA,
    "central america": _CENTRAL_AMERICA,
    "caribbean": _CARIBBEAN,
    "north america": _NORTH_AMERICA,
    "americas": tuple(dict.fromkeys(_NORTH_AMERICA + _CENTRAL_AMERICA + _CARIBBEAN +
                                    _SOUTH_AMERICA)),
    "mena": _MENA,
    "gcc": ("AE", "BH", "KW", "OM", "QA", "SA"),
    "middle east": ("AE", "BH", "IL", "IQ", "IR", "JO", "KW", "LB", "OM", "PS", "QA", "SA",
                    "SY", "TR", "YE"),
    "levant": ("IL", "JO", "LB", "PS", "SY"),
    "africa": _AFRICA,
    "sub saharan africa": tuple(c for c in _AFRICA
                                if c not in ("DZ", "EG", "LY", "MA", "TN", "SD")),
}


# --- lookup construction ---------------------------------------------------------

_PRI = {"CODE2": 6, "COUNTRY": 5, "US_STATE": 5, "CODE2_PROV": 4, "CA_PROV": 4, "MACRO": 4,
        "WORLD": 4, "US_REGION": 4, "REMOTE": 3, "WORKPLACE": 3, "NOISE": 2, "CITY": 1}

_Entry = tuple[str, str, bool]  # (class, payload, upper_only)


def _build_lookup() -> dict[str, list[_Entry]]:
    lk: dict[str, list[_Entry]] = {}

    def _add(key: str, cls: str, payload: str, upper_only: bool) -> None:
        entries = lk.setdefault(key, [])
        if any(e[0] == cls and e[1] == payload for e in entries):
            return
        entries.append((cls, payload, upper_only))
        entries.sort(key=lambda e: -_PRI[e[0]])

    def put(alias: str, cls: str, payload: str) -> None:
        # 2-3 letter ALL-CAPS aliases (US, UK, CAN, PER, AT...) only ever match
        # ALL-CAPS originals — otherwise prose 'at'/'per'/'can' become anchors.
        upper_only = (len(alias.replace(".", "")) <= 3 and alias == alias.upper()
                      and alias.replace(".", "").isalpha())
        key = _fold(alias)
        if not key:
            return
        _add(key, cls, payload, upper_only)
        if key.startswith("saint "):
            _add("st " + key[6:], cls, payload, upper_only)
        elif key.startswith("st "):
            _add("saint " + key[3:], cls, payload, upper_only)

    for iso, aliases in _COUNTRIES.items():
        for alias in aliases:
            # ISO2 codes colliding with US state codes resolve via CODE2 context.
            if len(alias) == 2 and alias.upper() in _US_STATE_CODES:
                continue
            put(alias, "COUNTRY", iso)
        if len(iso) == 2 and iso not in _US_STATE_CODES and iso not in _CA_PROV_CODES:
            _add(_fold(iso), "COUNTRY", iso, True)
    for name, code in _US_STATES.items():
        put(name, "US_STATE", code)
    for code in _US_STATE_CODES:
        _add(_fold(code), "CODE2", code, True)
    for name, code in _CA_PROVINCES.items():
        put(name, "CA_PROV", code)
    for code in _CA_PROV_CODES:
        if not any(e[0] in ("CODE2", "COUNTRY") for e in lk.get(_fold(code), [])):
            _add(_fold(code), "CODE2_PROV", code, True)
    for alias, key in _MACRO_ALIASES.items():
        put(alias, "MACRO", key)
    for word in _WORLD:
        put(word, "WORLD", "worldwide")
    for region in _US_REGIONS:
        put(region, "US_REGION", "US")
    for marker in _REMOTE_MARKERS:
        put(marker, "REMOTE", "remote")
    for marker in _WORKPLACE_MARKERS:
        put(marker, "WORKPLACE", _fold(marker))
    for noise in _NOISE:
        key = _fold(noise)
        if key:
            _add(key, "NOISE", key, False)
    for iso, cities in _CITIES.items():
        for city in cities:
            put(city, "CITY", iso)

    # Two deliberate tie-breaks. Bare 'Georgia' on job boards is the US state
    # (the country reading is restored by GE context in parse_location); bare
    # 'New York' is the city (the state reading survives as 'New York, NY').
    lk["georgia"].sort(key=lambda e: 0 if e[0] == "US_STATE" else 1)
    lk["new york"].sort(key=lambda e: 0 if e[0] == "CITY" else 1)
    return lk


_LOOKUP = _build_lookup()


# --- tokenizer -------------------------------------------------------------------

_TZ_RE = re.compile(r"(?i)\b(utc|gmt)\s*[+±\-−]?\s*\d{1,2}(:\d{2})?|\btime\s?zones?\b|"
                    r"\btimezones?\b|\b(us|eu|uk) hours\b|[+±]\s*\d+\s*(hours|hrs|h)\b|"
                    r"\bcet\b|\bcest\b|\b(est|pst|cst|mst|edt|pdt) (time)?\b")
_ZIP_RE = re.compile(r"^\d{3}\s?\d{2}$|^\d{4,6}$|^\d{5}-\d{4}$")
_URL_RE = re.compile(
    r"(?i)https?://\S+|www\.\S+|\b[a-z0-9-]{2,}\.(com|io|co|ai|org|net|bio|tech|dev)\b\S*")
_NUM_LOC_RE = re.compile(r"(?i)^\d+\s+(ubicaciones|locations|localidades|offices|cities|roles)$")
# 'Remote - X' / 'Hybrid: X' prefixes split into their own segment; the negative
# lookahead keeps 'Remote-first/friendly/ok/only/based/work/eligible' whole.
_REMOTE_PREFIX_RE = re.compile(r"(?i)\b(remote|hybrid|onsite)\s*[-:]\s*"
                               r"(?!first\b|friendly\b|ok\b|only\b|based\b|work\b|eligible\b)")
_OR_RE = re.compile(r"\s+\bo[ru]\b\s+", re.IGNORECASE)
_LEAD_CONNECTIVE_RE = re.compile(r"(?i)^(or|and|ou|e|y)\s+")

_STOPWORDS = {"we", "you", "our", "is", "are", "with", "for", "that", "this", "will", "have",
              "has", "who", "they", "them", "your", "my", "his", "her", "its", "be", "was",
              "were", "been", "does", "did", "can", "could", "should", "would",
              "not", "no", "yes", "if", "but", "so", "as", "at", "by", "from", "into", "up",
              "down", "out", "over", "under", "per", "via", "vs", "etc", "eg", "ie",
              "salary", "equity", "bonus", "comp", "pay", "paying", "hiring", "apply",
              "years", "days", "week", "hours", "hour", "specified"}

_PLACE_CLASSES = {"COUNTRY", "US_STATE", "CA_PROV", "CITY", "CITY?", "MACRO", "WORLD",
                  "US_REGION", "SUBDIV"}
_ANCHOR_CLASSES = {"COUNTRY", "US_STATE", "CA_PROV", "CITY"}
_CHAIN_ANCHORS = _ANCHOR_CLASSES | {"CITY?", "SUBDIV"}


def _place_shaped(orig_phrase: str, max_words: int = 5) -> bool:
    """Guard against laundering prose into places via absorption."""
    words = orig_phrase.split()
    if not words or len(words) > max_words:
        return False
    if any(re.search(r"\d", w) for w in words):
        return False
    if any(w.lower().strip(".,()") in _STOPWORDS for w in words):
        return False
    first = words[0]
    if not (first[0].isupper() or ord(first[0]) > 127):
        return False
    if len(orig_phrase) < 3 or len(orig_phrase) > 48:
        return False
    return True


class _Tok:
    __slots__ = ("raw", "cls", "payload", "src", "seg", "part")

    def __init__(self, raw: str, cls: str, payload: str | None = None, src: str = "") -> None:
        self.raw, self.cls, self.payload, self.src = raw, cls, payload, src
        self.seg = 0
        self.part = 0

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.cls}({self.raw!r}->{self.payload})"


def _lookup_hit(folded: str, orig: str) -> tuple[str, str] | None:
    entries = _LOOKUP.get(folded)
    if not entries:
        return None
    base = re.sub(r"[^A-Za-z]", "", orig)
    is_upper = bool(base) and base.isupper()
    for cls, payload, upper_only in entries:
        if upper_only and not is_upper:
            continue
        return cls, payload
    return None


def _classify_part(part: str) -> list[_Tok]:
    p = part.strip().strip(".").strip()
    if not p:
        return []
    if _URL_RE.search(p):
        return [_Tok(p, "URL")]
    if _NUM_LOC_RE.match(p):
        return [_Tok(p, "NOISE", "n-locations")]
    if _TZ_RE.search(p) and len(p) < 60:
        residue = _TZ_RE.sub(" ", p)
        residue = re.sub(r"(?i)\b(to|from|between|and|within|or)\b|[()+±\-−\d:,]", " ", residue)
        residue = residue.strip()
        toks = [_Tok(p, "TZ")]
        if residue:
            hit = _lookup_hit(_fold(residue), residue)
            if hit:
                toks.append(_Tok(residue, hit[0], hit[1]))
            else:
                toks.append(_Tok(residue, "UNK"))
        return toks
    folded = _fold(p)
    if not folded:
        return []
    if _ZIP_RE.match(folded.replace(" ", "")):
        return [_Tok(p, "ZIP")]
    hit = _lookup_hit(folded, p)
    if hit:
        return [_Tok(p, hit[0], hit[1])]
    # Word-wise greedy sweep; one original word may fold to several words.
    pairs: list[tuple[str, str]] = []
    for orig_word in p.split():
        for folded_word in _fold(orig_word).split():
            pairs.append((orig_word, folded_word))
    toks: list[_Tok] = []
    unk: list[str] = []
    i = 0
    while i < len(pairs):
        matched = False
        for length in range(min(5, len(pairs) - i), 0, -1):
            phrase_f = " ".join(fw for _, fw in pairs[i:i + length])
            phrase_o = " ".join(ow for ow, _ in pairs[i:i + length])
            hit = _lookup_hit(phrase_f, phrase_o)
            if hit:
                if unk:
                    toks.append(_Tok(" ".join(unk), "UNK"))
                    unk = []
                toks.append(_Tok(phrase_o, hit[0], hit[1], src="sweep"))
                i += length
                matched = True
                break
        if not matched:
            orig_word = pairs[i][0]
            if _ZIP_RE.match(pairs[i][1].replace(" ", "")):
                toks.append(_Tok(orig_word, "ZIP"))
            else:
                unk.append(orig_word)
            i += 1
    if unk:
        toks.append(_Tok(" ".join(unk), "UNK"))
    return toks


def _anchor_country(tok: _Tok) -> str | None:
    if tok.cls in ("COUNTRY", "CITY", "CITY?", "SUBDIV"):
        return tok.payload
    if tok.cls == "US_STATE":
        return "US"
    if tok.cls == "CA_PROV":
        return "CA"
    return None


def _segments_of(raw: str) -> list[str]:
    s = html.unescape(raw)
    s = s.replace(" ", " ").replace("\x00", " ").replace("\x01", " ")
    s = re.sub(r"[–—]", "-", s)
    s = s.replace("\n", ";").replace("\r", ";")
    s = _URL_RE.sub(" \x00 ", s)
    s = _REMOTE_PREFIX_RE.sub(lambda m: m.group(1) + ";", s)
    s = _OR_RE.sub(";", s)
    for sep in ["|", "•", "·", ";", "/", "(", ")", "[", "]"]:
        s = s.replace(sep, "\x01")
    s = re.sub(r"\s-\s", "\x01", s)
    # The URL placeholder must survive as its own segment-shaped token so a link
    # marks the string unclean instead of word-sweeping into UNK spam.
    s = s.replace("\x00", " \x01http://x\x01 ")
    return [seg.strip() for seg in s.split("\x01") if seg.strip()]


def _tokenize(raw: str) -> list[_Tok]:
    all_toks: list[_Tok] = []
    for seg_i, seg in enumerate(_segments_of(raw)):
        seg = seg.strip().strip(",").strip()
        if not seg:
            continue
        seg = _LEAD_CONNECTIVE_RE.sub("", seg)
        groups = [g for g in (_classify_part(pp) for pp in seg.split(",")) if g]
        # Comma-anchor absorption R->L, innermost first, so chains resolve
        # ('Umuarama, Parana, Brasil'; 'Barueri, SP, Sao Paulo, Brasil').
        for gi in range(len(groups) - 2, -1, -1):
            group = groups[gi]
            if not all(t.cls == "UNK" for t in group):
                continue
            target = None
            for gj in range(gi + 1, len(groups)):
                anchors = [t for t in groups[gj] if t.cls in _CHAIN_ANCHORS]
                if anchors:
                    target = anchors[0]
                    break
                if any(t.cls == "UNK" for t in groups[gj]):
                    break
            if target is None:
                continue
            country = _anchor_country(target)
            if country is None:
                continue
            raw0 = group[0].raw.strip()
            if (len(group) == 1 and len(raw0) in (2, 3) and raw0.isupper()
                    and raw0.isalpha() and country != "US"):
                # 'City, SP, Brasil' — an ATS regional code of a non-US country.
                group[0].cls, group[0].payload, group[0].src = "SUBDIV", country, "comma-R"
            elif all(_place_shaped(t.raw) for t in group):
                for t in group:
                    t.cls, t.payload, t.src = "CITY?", country, "comma-R"
        # L->R: 'Country, City'.
        for gi in range(len(groups) - 1, 0, -1):
            group = groups[gi]
            if all(t.cls == "UNK" for t in group) and all(_place_shaped(t.raw) for t in group):
                prev = [t for t in groups[gi - 1] if t.cls in _ANCHOR_CLASSES]
                if prev:
                    country = _anchor_country(prev[-1])
                    for t in group:
                        t.cls, t.payload, t.src = "CITY?", country, "comma-L"
        # Leftover UNK next to an anchor within the same part.
        for group in groups:
            anchors = [t for t in group if t.cls in _ANCHOR_CLASSES]
            unks = [t for t in group if t.cls == "UNK"]
            if anchors and 0 < len(unks) <= 2:
                country = _anchor_country(anchors[0])
                for t in unks:
                    if _place_shaped(t.raw, max_words=4):
                        t.cls, t.payload, t.src = "CITY?", country, "inpart"
        for part_i, group in enumerate(groups):
            for t in group:
                t.seg, t.part = seg_i, part_i
                all_toks.append(t)

    # 2-letter code resolution, context-aware: CA context → province; BR context
    # (a Brazil country word or a BR gazetteer city) → Brazilian state code
    # ('Campo Grande, MS, Brasil' — MS is Mato Grosso do Sul, not Mississippi);
    # a city of that ISO2 present and no US evidence → country ('IN - Bangalore'
    # → India, 'Toronto, CA' → Canada); default → US state.
    city_isos = {t.payload for t in all_toks if t.cls == "CITY"}
    has_ca = any((t.cls == "COUNTRY" and t.payload == "CA") or t.cls == "CA_PROV"
                 for t in all_toks)
    has_br = "BR" in city_isos or any(
        t.cls == "COUNTRY" and t.payload == "BR" for t in all_toks
    )
    us_evidence = any((t.cls == "COUNTRY" and t.payload == "US") or t.cls == "US_STATE"
                      or (t.cls == "CITY" and t.payload == "US") or t.cls == "US_REGION"
                      for t in all_toks)
    for t in all_toks:
        if t.cls == "CODE2":
            if has_ca and t.payload in _CA_PROV_CODES:
                t.cls = "CA_PROV"
            elif has_br and not us_evidence and t.payload in _BR_STATE_CODES:
                t.cls, t.payload = "SUBDIV", "BR"
            elif t.payload in city_isos and t.payload != "US" and not us_evidence:
                t.cls = "COUNTRY"
            else:
                t.cls = "US_STATE"
        elif t.cls == "CODE2_PROV":
            if has_ca:
                t.cls = "CA_PROV"
            else:
                iso = {"NL": "NL", "PE": "PE", "SK": "SK"}.get(t.payload or "")
                if iso:
                    t.cls, t.payload = "COUNTRY", iso
                else:
                    t.cls = "CA_PROV"

    # 'Georgia' flips back to the country when Georgian context is present.
    if any(t.cls == "CITY" and t.payload == "GE" for t in all_toks):
        for t in all_toks:
            if t.cls == "US_STATE" and t.payload == "GA" and _fold(t.raw) == "georgia":
                t.cls, t.payload = "COUNTRY", "GE"

    # Cross-segment absorption: place-shaped UNK segments adopt the first anchor.
    anchor = next((t for t in all_toks if t.cls in _ANCHOR_CLASSES), None)
    if anchor:
        country = _anchor_country(anchor)
        for t in all_toks:
            if t.cls == "UNK" and _place_shaped(t.raw, max_words=3):
                t.cls, t.payload, t.src = "CITY?", country, "xseg"
    return all_toks


# --- unit grouping and the parse itself -------------------------------------------


_CONF_RANK = {"high": 0, "medium": 1, "low": 2}


def _worse(a: str, b: str) -> str:
    return a if _CONF_RANK[a] >= _CONF_RANK[b] else b


class _Unit:
    __slots__ = ("city", "subdivision", "country", "macro", "confidence", "raws", "seg",
                 "explicit_country")

    def __init__(self, seg: int) -> None:
        self.city: str | None = None
        self.subdivision: str | None = None
        self.country: str | None = None
        self.macro: str | None = None
        self.confidence = "high"
        self.raws: list[str] = []
        self.seg = seg
        self.explicit_country = False

    def to_place(self) -> GeoPlace:
        return GeoPlace(
            city=self.city,
            subdivision=self.subdivision,
            country=self.country,
            macro=self.macro,
            confidence=self.confidence,
            raw=" ".join(self.raws),
        )


def _tok_confidence(tok: _Tok) -> str:
    if tok.cls == "CITY?":
        return "low" if tok.src == "xseg" else "medium"
    if tok.cls == "SUBDIV":
        return "medium"
    return "high"


def _group_units(toks: list[_Tok]) -> tuple[list[_Unit], bool, bool, set[int], set[int]]:
    """Walk tokens per segment, combining city/subdivision/country parts into units."""
    units: list[_Unit] = []
    remote_marker = False
    worldwide = False
    remote_segs: set[int] = set()
    city_segs: set[int] = set()
    open_unit: _Unit | None = None
    current_seg = -1

    for tok in toks:
        if tok.seg != current_seg:
            current_seg = tok.seg
            open_unit = None
        cls = tok.cls
        if cls in ("CITY", "CITY?"):
            unit = _Unit(tok.seg)
            unit.city = tok.raw
            unit.country = tok.payload
            unit.confidence = _tok_confidence(tok)
            unit.raws.append(tok.raw)
            units.append(unit)
            open_unit = unit
            city_segs.add(tok.seg)
        elif cls in ("US_STATE", "CA_PROV"):
            code = tok.payload or ""
            country = "US" if cls == "US_STATE" else "CA"
            if open_unit is not None and open_unit.subdivision is None:
                open_unit.subdivision = code
                if open_unit.country != country:
                    if open_unit.country is not None:
                        # Explicit state overrides a city-table-inferred country
                        # ('Paris, TX'), at reduced confidence.
                        open_unit.confidence = _worse(open_unit.confidence, "medium")
                    open_unit.country = country
                open_unit.explicit_country = True
                open_unit.raws.append(tok.raw)
            else:
                unit = _Unit(tok.seg)
                unit.subdivision = code
                unit.country = country
                unit.explicit_country = True
                unit.confidence = _tok_confidence(tok)
                unit.raws.append(tok.raw)
                units.append(unit)
                open_unit = unit
        elif cls == "SUBDIV":
            if (open_unit is not None and open_unit.subdivision is None
                    and open_unit.country in (None, tok.payload)):
                open_unit.subdivision = tok.raw.upper()
                open_unit.country = tok.payload
                open_unit.confidence = _worse(open_unit.confidence, "medium")
                open_unit.raws.append(tok.raw)
            else:
                unit = _Unit(tok.seg)
                unit.subdivision = tok.raw.upper()
                unit.country = tok.payload
                unit.confidence = "medium"
                unit.raws.append(tok.raw)
                units.append(unit)
                open_unit = unit
        elif cls == "COUNTRY":
            if open_unit is not None and open_unit.country == tok.payload:
                open_unit.explicit_country = True
                open_unit.raws.append(tok.raw)
            elif open_unit is not None and open_unit.country is None:
                open_unit.country = tok.payload
                open_unit.explicit_country = True
                open_unit.raws.append(tok.raw)
            elif (open_unit is not None and open_unit.city is not None
                  and not open_unit.explicit_country):
                # Explicit country overrides the city table ('London, Canada').
                open_unit.country = tok.payload
                open_unit.explicit_country = True
                open_unit.confidence = _worse(open_unit.confidence, "medium")
                open_unit.raws.append(tok.raw)
            else:
                unit = _Unit(tok.seg)
                unit.country = tok.payload
                unit.explicit_country = True
                unit.raws.append(tok.raw)
                units.append(unit)
                open_unit = unit
        elif cls == "US_REGION":
            unit = _Unit(tok.seg)
            unit.country = "US"
            unit.raws.append(tok.raw)
            units.append(unit)
        elif cls == "MACRO":
            unit = _Unit(tok.seg)
            unit.macro = tok.payload
            unit.raws.append(tok.raw)
            units.append(unit)
        elif cls == "WORLD":
            worldwide = True
        elif cls == "REMOTE":
            remote_marker = True
            remote_segs.add(tok.seg)
        # WORKPLACE / NOISE / TZ / ZIP / URL / UNK never form units.
    return units, remote_marker, worldwide, remote_segs, city_segs


def parse_location(raw: str) -> LocationParse:
    """Parse one raw location string. Pure; never raises on garbage input."""
    if not raw or not raw.strip():
        return LocationParse()
    toks = _tokenize(raw)
    if not toks:
        return LocationParse()
    units, remote_marker, worldwide, remote_segs, city_segs = _group_units(toks)
    unknown = tuple(t.raw for t in toks if t.cls in ("UNK", "URL"))[:6]
    has_marker = remote_marker or any(t.cls == "WORKPLACE" for t in toks)

    # Marker-only segments (a remote marker and nothing geo) extend the fence
    # context to their neighbors: 'Remote - US', 'United States (Remote)'.
    geo_segs = {u.seg for u in units}
    marker_only_segs = remote_segs - geo_segs - city_segs
    fence_segs = set(remote_segs)
    for seg in marker_only_segs:
        fence_segs.add(seg - 1)
        fence_segs.add(seg + 1)

    fence: list[GeoPlace] = []
    places: list[GeoPlace] = []
    for unit in units:
        is_fence = (unit.city is None and unit.subdivision is None
                    and (unit.country is not None or unit.macro is not None)
                    and unit.seg in fence_segs
                    and unit.seg not in city_segs)
        if is_fence:
            fence.append(unit.to_place())
        else:
            places.append(unit.to_place())

    # A bare country mention is redundant once a city/subdivision place names the
    # same country ('San Francisco, CA • New York, NY • United States').
    covered = {p.country for p in places if p.country and (p.city or p.subdivision)}
    places = [p for p in places
              if not (p.city is None and p.subdivision is None and p.macro is None
                      and p.country in covered)]

    def dedup(entries: list[GeoPlace]) -> tuple[GeoPlace, ...]:
        seen: set[tuple[str | None, ...]] = set()
        out: list[GeoPlace] = []
        for entry in entries:
            key = (_fold(entry.city) if entry.city else None, entry.subdivision,
                   entry.country, entry.macro)
            if key in seen:
                continue
            seen.add(key)
            out.append(entry)
        return tuple(out)

    places_t = dedup(places)
    fence_t = dedup(fence)

    clean = not unknown
    has_country_geo = any(p.country for p in places_t) or any(f.country for f in fence_t)
    has_macro = worldwide or any(p.macro for p in places_t) or any(f.macro for f in fence_t)
    if clean:
        if has_country_geo:
            quality = "full"
        elif has_macro:
            quality = "macro"
        elif has_marker:
            quality = "marker"
        else:
            quality = "noise"
    else:
        quality = "partial" if (has_country_geo or has_macro) else "fail"

    return LocationParse(
        places=places_t,
        fence=fence_t,
        remote_marker=remote_marker,
        worldwide=worldwide,
        quality=quality,
        unknown=unknown,
    )
