"""Contract tests for the deterministic location parser (§4.12).

The parser is the rules layer of the structured-locations pipeline: it turns the
raw ``positions.location`` string (NEVER ``location_normalized`` — §4.6 folds the
multi-location separators the parser needs) into structured places, a remote
fence, and a parse quality that decides whether the §4.7 LLM tail ever sees the
string. The corpus below is ported from the validated feasibility simulation
(97.0% of live rows parse clean); the adversarial cases are load-bearing — naive
variants of the guards launder prose into fake places.
"""

from __future__ import annotations

import dataclasses

import pytest

from jobcrawler.runtime import geo


def countries_of(parse: geo.LocationParse) -> list[str]:
    return sorted({p.country for p in parse.places if p.country})


def fence_countries(parse: geo.LocationParse) -> list[str]:
    return sorted({f.country for f in parse.fence if f.country})


def fence_macros(parse: geo.LocationParse) -> list[str]:
    return sorted({f.macro for f in parse.fence if f.macro})


def cities_of(parse: geo.LocationParse) -> list[str]:
    return sorted({p.city for p in parse.places if p.city})


# --- basic shapes -----------------------------------------------------------


def test_city_state_pair() -> None:
    parse = geo.parse_location("Portland, OR")
    assert len(parse.places) == 1
    place = parse.places[0]
    assert (place.city, place.subdivision, place.country) == ("Portland", "OR", "US")
    assert parse.fence == ()
    assert not parse.remote_marker
    assert parse.quality == "full"


def test_country_only_list_yields_one_place_per_country() -> None:
    parse = geo.parse_location("Spain, Portugal, Germany")
    assert countries_of(parse) == ["DE", "ES", "PT"]
    assert all(p.city is None for p in parse.places)
    assert parse.quality == "full"


def test_ambiguous_four_part_comma_list_pairs_cities_with_states() -> None:
    # 'City, ST, City, ST' — the comma is both the list separator and the
    # city-state joiner; right-to-left absorption must yield two places, not four.
    parse = geo.parse_location("New York, NY, San Francisco, CA")
    assert cities_of(parse) == ["New York", "San Francisco"]
    assert countries_of(parse) == ["US"]
    assert len(parse.places) == 2


def test_city_region_country_triple() -> None:
    parse = geo.parse_location("Milan, Lombardy, Italy")
    assert countries_of(parse) == ["IT"]
    assert "Milan" in cities_of(parse)
    assert parse.quality == "full"


def test_multi_list_separators() -> None:
    for raw, want in [
        ("San Francisco, CA | New York City, NY", 2),  # anthropic pipes
        ("Doha, Qatar; London, UK", 2),  # scale semicolons
        ("SG - Singapore / MY - Kuala Lumpur", 2),  # airwallex slash + prefix codes
    ]:
        parse = geo.parse_location(raw)
        assert len(parse.places) == want, raw
        assert parse.quality == "full", raw


# --- remote fences ----------------------------------------------------------


def test_remote_dash_country_is_a_fence_not_a_place() -> None:
    parse = geo.parse_location("Remote - US")
    assert parse.places == ()
    assert fence_countries(parse) == ["US"]
    assert parse.remote_marker
    assert not parse.worldwide
    assert parse.quality == "full"


@pytest.mark.parametrize(
    "raw",
    [
        "Remote, US",
        "Remote US",
        "Remote - United States",
        "United States (Remote)",
        "Remote, US Only",
        "Remote: United States",
    ],
)
def test_geo_fenced_remote_variants_all_normalize_to_a_us_fence(raw: str) -> None:
    parse = geo.parse_location(raw)
    assert fence_countries(parse) == ["US"], raw
    assert parse.places == (), raw
    assert parse.remote_marker, raw


def test_country_word_next_to_remote_in_one_segment_is_a_fence() -> None:
    # remotecom style: 'Austria Remote' / 'Remote, Italy'
    assert fence_countries(geo.parse_location("Austria Remote")) == ["AT"]
    assert fence_countries(geo.parse_location("Remote, Italy")) == ["IT"]


def test_gitlab_semicolon_fence_list() -> None:
    parse = geo.parse_location("Remote, Canada; Remote, Ireland")
    assert fence_countries(parse) == ["CA", "IE"]
    assert parse.places == ()


def test_remote_in_us_and_can() -> None:
    parse = geo.parse_location("Remote in US & CAN")
    assert fence_countries(parse) == ["CA", "US"]


def test_macro_fence() -> None:
    parse = geo.parse_location("Home based - EMEA")
    assert fence_macros(parse) == ["emea"]
    assert parse.remote_marker
    assert parse.quality == "macro"
    assert fence_macros(geo.parse_location("Remote-Iberia")) == ["iberia"]


def test_mixture_offices_or_fenced_remote() -> None:
    # monzo style: the operator's headline case — onsite places AND a fence.
    parse = geo.parse_location("Cardiff, London or Remote (UK)")
    assert cities_of(parse) == ["Cardiff", "London"]
    assert countries_of(parse) == ["GB"]
    assert fence_countries(parse) == ["GB"]
    assert parse.remote_marker


def test_city_bearing_segment_with_marker_stays_a_place() -> None:
    # A city next to the marker is an office you could work from, not a fence.
    parse = geo.parse_location("San Francisco, CA or Remote")
    assert cities_of(parse) == ["San Francisco"]
    assert parse.fence == ()
    assert parse.remote_marker


def test_us_region_fence_resolves_to_country_level() -> None:
    parse = geo.parse_location("United States-East Coast (remote)")
    assert fence_countries(parse) == ["US"]


# --- worldwide / markers ----------------------------------------------------


def test_worldwide_variants() -> None:
    for raw in ["Anywhere in the World", "Home based - Worldwide", "Global", "Anywhere"]:
        parse = geo.parse_location(raw)
        assert parse.worldwide, raw
        assert parse.quality == "macro", raw
    parse = geo.parse_location("Remote (Worldwide); Worldwide")
    assert parse.worldwide and parse.remote_marker


def test_bare_marker_strings_are_marker_quality() -> None:
    # No fence, no places: these are exactly the §4.7 marker-arm LLM tail.
    for raw in ["Remote", "Hybrid", "In-Office", "Distributed"]:
        parse = geo.parse_location(raw)
        assert parse.places == () and parse.fence == (), raw
        assert parse.quality == "marker", raw
    assert geo.parse_location("Remote").remote_marker
    assert not geo.parse_location("Hybrid").remote_marker


def test_remote_first_is_a_marker_not_a_fence_split() -> None:
    # 'Remote-first' must not split as 'Remote - <place>'.
    parse = geo.parse_location("Remote-first")
    assert parse.remote_marker
    assert parse.fence == ()
    assert parse.quality == "marker"


def test_tz_fence_residue() -> None:
    parse = geo.parse_location("Anywhere (GMT +2 to GMT -8)")
    assert parse.worldwide
    assert parse.quality == "macro"


def test_global_token_glued_to_concrete_geography_is_not_worldwide() -> None:
    # audit #143: a world token sharing its string with real geography is an
    # aggregator's flourish on a fenced role, not a worldwide reach. The
    # concrete place wins so the projection never over-claims 'anywhere'.
    for raw in [
        "Anywhere, United States",
        "Global, SP, BR",
        "United States; Worldwide",
        "Anywhere; Europe",
    ]:
        parse = geo.parse_location(raw)
        assert not parse.worldwide, raw
        assert parse.places or parse.fence, raw


def test_bare_global_token_still_reads_worldwide() -> None:
    # No competing geography: the worldwide claim stands (the eu_remote_jobs
    # 'Remote - Worldwide' output and the WWR canon must survive the v4 change).
    for raw in ["Worldwide", "Anywhere", "Remote - Worldwide", "Home based - Worldwide"]:
        parse = geo.parse_location(raw)
        assert parse.worldwide, raw
        assert parse.places == () and parse.fence == (), raw


# --- named disambiguation regressions ---------------------------------------


def test_toronto_ca_reads_as_canada() -> None:
    parse = geo.parse_location("Toronto, CA")
    assert countries_of(parse) == ["CA"]
    assert cities_of(parse) == ["Toronto"]
    assert all(p.subdivision is None for p in parse.places)


def test_in_prefix_reads_as_india_not_indiana() -> None:
    parse = geo.parse_location("IN - Bangalore")
    assert countries_of(parse) == ["IN"]
    assert cities_of(parse) == ["Bangalore"]


def test_hk_prefix_code() -> None:
    parse = geo.parse_location("HK - Hong Kong SAR")
    assert countries_of(parse) == ["HK"]
    assert parse.quality == "full"


def test_nubank_inverted_country_city() -> None:
    parse = geo.parse_location("USA, Palo Alto")
    assert len(parse.places) == 1
    assert parse.places[0].city == "Palo Alto"
    assert parse.places[0].country == "US"
    parse = geo.parse_location("USA, Miami; USA, Palo Alto")
    assert cities_of(parse) == ["Miami", "Palo Alto"]
    assert len(parse.places) == 2


def test_redundant_trailing_country_mention_is_absorbed() -> None:
    # figma bullets: the trailing 'United States' repeats the cities' country.
    parse = geo.parse_location("San Francisco, CA • New York, NY • United States")
    assert cities_of(parse) == ["New York", "San Francisco"]
    assert len(parse.places) == 2


def test_brazilian_regional_code_chain() -> None:
    # 'City, SP, São Paulo, Brasil' — 'SP' is a BR state code, not a US guess.
    parse = geo.parse_location("Barueri, SP, São Paulo, Brasil")
    assert countries_of(parse) == ["BR"]
    assert any(p.subdivision == "SP" for p in parse.places)


def test_brazilian_state_codes_that_collide_with_us_codes() -> None:
    # MS/SC/MA/PA/MT/AL are BOTH US state codes and Brazilian ones. Brazilian
    # context (a BR country word or a BR gazetteer city) must win; without any
    # context the US reading stands.
    parse = geo.parse_location("Campo Grande, MS, Brasil")
    assert countries_of(parse) == ["BR"]
    assert len(parse.places) == 1
    assert (parse.places[0].city, parse.places[0].subdivision) == ("Campo Grande", "MS")

    parse = geo.parse_location("Florianopolis, SC")  # BR city-table context
    assert countries_of(parse) == ["BR"]

    parse = geo.parse_location("Jackson, MS")  # no BR context → Mississippi
    assert countries_of(parse) == ["US"]


def test_bare_georgia_is_the_us_state() -> None:
    # Documented acceptance: bare 'Georgia' on job boards is the US state.
    parse = geo.parse_location("Georgia")
    assert len(parse.places) == 1
    assert (parse.places[0].subdivision, parse.places[0].country) == ("GA", "US")


def test_georgia_with_georgian_context_is_the_country() -> None:
    parse = geo.parse_location("Tbilisi, Georgia")
    assert countries_of(parse) == ["GE"]
    assert cities_of(parse) == ["Tbilisi"]


def test_bare_washington_is_the_state() -> None:
    parse = geo.parse_location("Washington")
    assert (parse.places[0].subdivision, parse.places[0].country) == ("WA", "US")


def test_bare_unknown_town_never_guesses_a_country() -> None:
    parse = geo.parse_location("Hawthorne")
    assert parse.places == ()
    assert parse.quality == "fail"


def test_prose_words_never_match_short_uppercase_aliases() -> None:
    # 'at'/'per'/'can' must not hit AT/PE/CAN — the upper_only guard.
    parse = geo.parse_location("Apply at our careers page per the instructions")
    assert countries_of(parse) == []
    assert parse.quality == "fail"


# --- garbage in, structure never invented ------------------------------------


def test_url_blurbs_fail_without_raising() -> None:
    blob = (
        "SmarterDx / Remote (US only) / https://smarterdx.com/careers SmarterDx "
        "builds clinical AI for one of healthcare's most broken systems: how "
        "hospitals get paid for the care they deliver."
    )
    parse = geo.parse_location(blob)
    assert parse.quality in ("partial", "fail")


def test_mojibake_and_non_latin_never_raise() -> None:
    for raw in ["SÃ£o Paulo", "東京", "Тель-Авив", "Nagpur,", "\x00\x01weird"]:
        parse = geo.parse_location(raw)
        assert parse.quality in ("full", "macro", "marker", "noise", "partial", "fail")


def test_noise_only_strings() -> None:
    for raw in ["Not specified", "Multiple locations", "HQ", ""]:
        parse = geo.parse_location(raw)
        assert parse.places == () and parse.fence == ()
        assert parse.quality == "noise", raw


# --- purity / API invariants -------------------------------------------------


def test_parse_is_deterministic_and_frozen() -> None:
    a = geo.parse_location("Remote - US; Berlin, Germany")
    b = geo.parse_location("Remote - US; Berlin, Germany")
    assert a == b
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(a, "quality", "fail")
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(a.places[0], "city", "x")


def test_quality_vocabulary_is_closed_over_a_wide_corpus() -> None:
    corpus = [
        "USA",
        "San Francisco",
        "Remote",
        "Hybrid",
        "SG - Singapore",
        "Home based - Worldwide",
        "Bengaluru, India",
        "New York, NY (HQ)",
        "Vienna, AT; AT",
        "Mexico, Mexico City",
        "Trinidad and Tobago",
        "Greater Chicago Area",
        "Boston Metro Area",
        "London Office",
        "Vancouver, BC, Canada, New York, NY, USA",
        "Portugal Remote",
        "NY New York US",
        "Winston-Salem, North Carolina, USA",
        "San Francisco, New York",
        "Umuarama, Parana, Brasil",
        "UAE, Dubai",
    ]
    for raw in corpus:
        parse = geo.parse_location(raw)
        assert parse.quality in ("full", "macro", "marker", "noise", "partial", "fail"), raw
        for place in parse.places:
            if place.country is not None:
                assert place.country in geo.COUNTRY_CODES, raw
        for entry in parse.fence:
            assert (entry.country is None) != (entry.macro is None), raw
            if entry.country is not None:
                assert entry.country in geo.COUNTRY_CODES, raw
            if entry.macro is not None:
                assert entry.macro in geo.MACRO_KEYS, raw


def test_hq_suffix_stays_clean() -> None:
    parse = geo.parse_location("New York, NY (HQ)")
    assert cities_of(parse) == ["New York"]
    assert parse.quality == "full"


# --- display helpers ---------------------------------------------------------


def test_country_name_and_macro_label() -> None:
    assert geo.country_name("US") == "United States"
    assert geo.country_name("GB") == "United Kingdom"
    assert geo.country_name("BR") == "Brazil"
    assert geo.country_name(None) is None
    assert geo.country_name("ZZ") is None
    assert geo.macro_label("emea") == "EMEA"
    assert geo.macro_label("latam") == "LATAM"
    assert geo.macro_label("worldwide") == "Worldwide"
    assert geo.macro_label(None) is None


def test_place_display_is_human_readable_with_english_country_names() -> None:
    parse = geo.parse_location("São Paulo, Brazil")
    assert geo.place_display(parse.places[0]) == "São Paulo, Brazil"
    parse = geo.parse_location("Portland, OR")
    assert geo.place_display(parse.places[0]) == "Portland, OR, United States"
    parse = geo.parse_location("Spain")
    assert geo.place_display(parse.places[0]) == "Spain"
    parse = geo.parse_location("Home based - EMEA")
    assert geo.place_display(parse.fence[0]) == "EMEA"


# --- region members (macro expansion for remote_in filters) ------------------


def test_region_members_vocabulary_is_consistent() -> None:
    assert geo.GEO_REGION_SEED_VERSION >= 1
    for region, members in geo.GEO_REGION_MEMBERS.items():
        assert region in geo.MACRO_KEYS, region
        assert members, region
        for iso in members:
            assert iso in geo.COUNTRY_CODES, (region, iso)


def test_region_members_spot_checks() -> None:
    members = geo.GEO_REGION_MEMBERS
    assert "DE" in members["eu"]
    assert "GB" not in members["eu"]  # Brexit is data now
    assert "GB" in members["europe"]
    assert "NO" in members["nordics"]
    assert "NO" not in members["eu"]
    assert "BR" in members["latam"]
    assert "BR" in members["south america"]
    assert "AE" in members["gcc"]
    assert "AE" in members["mena"]
    assert "US" in members["north america"]
    assert "US" in members["americas"]
    assert "DE" in members["dach"]
    assert "PT" in members["iberia"]
    assert "IE" in members["uki"]
    assert "SG" in members["apac"]
    assert "SG" in members["southeast asia"]


def test_title_fence_reads_explicit_only_phrases() -> None:
    # Posters put the fence in the TITLE while the board's region field claims
    # worldwide ('[EUROPE ONLY] …' filed under 'Anywhere in the World'). An
    # explicit '<place> only' in a title segment is a fence; 'only' without
    # geography in the same segment never is.
    fence = geo.title_fence(
        "[EUROPE ONLY] GTM MarTech Engineer (Growth & Attribution) at Video MarTech SaaS"
    )
    assert [f.macro for f in fence] == ["europe"]
    fence = geo.title_fence("Senior Software Engineer - Backend/Python - USA Only (100% Remote)")
    assert [f.country for f in fence] == ["US"]
    fence = geo.title_fence("Embedded Linux Field Engineering Manager (Americas only)")
    assert [f.macro for f in fence] == ["americas"]
    assert geo.title_fence("Read-Only Systems Engineer") == ()
    assert geo.title_fence("SQL Only Engineer") == ()
    # 'only' in one segment, geography in another → no fence.
    assert geo.title_fence("Engineer (Berlin) - GmbH only") == ()
    assert geo.title_fence("Senior Rust Engineer") == ()


def test_parser_version_pinned() -> None:
    # Bumping GEO_PARSER_VERSION triggers a whole-corpus deterministic resweep at
    # store open — any behavior change in this module must come with a bump.
    # v2: the Brazilian state-code context rule; v3: title fences demote false
    # worldwide projections; v4: a global token co-occurring with concrete
    # geography no longer reads as worldwide.
    assert geo.GEO_PARSER_VERSION == 4
