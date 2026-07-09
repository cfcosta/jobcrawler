"""Tests for host-side deterministic enrichment (§4.6/§8).

Pure in→out classification (discipline × level × workplace) and conservative
salary parsing, covering EN + pt-BR titles across every enum value, workplace
precedence, and the four empirical salary formats plus ambiguity→None. This is
the fleet-wide safety net: a boundary regression here is caught before it ships,
without regenerating any crawler.
"""

from __future__ import annotations

import pytest

from jobcrawler.runtime.contract import Job
from jobcrawler.runtime.normalize import (
    DISCIPLINES,
    LEVELS,
    REMOTE_SCOPES,
    WORKPLACES,
    SalaryRange,
    canonical_company,
    classify_discipline,
    classify_level,
    classify_relocation,
    classify_remote_scope,
    classify_workplace,
    enrich,
    normalize_location,
    parse_salary,
    usd_annual_band,
)


# --- discipline (EN + pt-BR, every enum value) -----------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # backend
        ("Backend Engineer", "backend"),
        ("Desenvolvedor(a) Backend Java - Junior", "backend"),
        ("Server-Side Developer", "backend"),
        # frontend
        ("Frontend Developer", "frontend"),
        ("Pessoa Desenvolvedora Frontend Sênior", "frontend"),
        ("React Engineer", "frontend"),
        # fullstack
        ("Full Stack Engineer", "fullstack"),
        ("Desenvolvedor Full-Stack", "fullstack"),
        # mobile
        ("iOS Engineer", "mobile"),
        ("Staff Software Engineer | iOS", "mobile"),
        ("Android Developer", "mobile"),
        ("React Native Developer", "mobile"),
        # devops / sre
        ("DevOps Engineer", "devops_sre"),
        ("Site Reliability Engineer", "devops_sre"),
        ("SRE", "devops_sre"),
        # data
        ("Data Engineer", "data"),
        ("Data Analyst", "data"),
        ("Analista de Dados", "data"),
        # ml / ai
        ("Machine Learning Engineer", "ml_ai"),
        ("Data Scientist", "ml_ai"),
        ("Cientista de Dados", "ml_ai"),
        # security
        ("Security Engineer", "security"),
        ("Application Security Engineer", "security"),
        # qa
        ("QA Engineer", "qa"),
        ("SDET", "qa"),
        ("Analista de Testes", "qa"),
        # embedded
        ("Embedded Software Engineer", "embedded"),
        ("Firmware Engineer", "embedded"),
        # eng_other (generic engineering, no specific discipline)
        ("Software Engineer", "eng_other"),
        ("Engineering Manager, Platform", "eng_other"),
        ("Solutions Architect", "eng_other"),
        ("Gerente de Engenharia", "eng_other"),
        ("Tech Lead", "eng_other"),
        # non_eng
        ("Product Designer", "non_eng"),
        ("UX Researcher", "non_eng"),
        ("Recruiter", "non_eng"),
        ("Account Executive", "non_eng"),
        ("Gerente de Produto", "non_eng"),
        ("Product Manager", "non_eng"),
    ],
)
def test_classify_discipline(title: str, expected: str) -> None:
    assert classify_discipline(title) in DISCIPLINES
    assert classify_discipline(title) == expected


def test_discipline_defaults_eng_other_when_opaque() -> None:
    # No discipline / eng / non-eng signal at all → recall-safe eng_other.
    assert classify_discipline("Platform Team Member") == "eng_other"


def test_discipline_uses_description_only_when_title_opaque() -> None:
    assert (
        classify_discipline("Platform Team", "You will own our backend APIs.")
        == "backend"
    )
    # An explicit title is never overridden by a stray stack mention in the JD.
    assert (
        classify_discipline("Backend Engineer", "Collaborate with the frontend team")
        == "backend"
    )


# --- level (EN + pt-BR, every enum value) ----------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # intern
        ("Software Engineer Intern", "intern"),
        ("Estágio em Desenvolvimento", "intern"),
        ("Estagiário de Backend", "intern"),
        # junior
        ("Junior Developer", "junior"),
        ("Desenvolvedor Júnior", "junior"),
        ("Backend Engineer Jr", "junior"),
        # mid
        ("Mid-Level Engineer", "mid"),
        ("Desenvolvedor Pleno", "mid"),
        ("Middle Software Engineer", "mid"),
        # senior
        ("Senior Engineer", "senior"),
        ("Engenheiro Sênior", "senior"),
        ("Sr. Backend Developer", "senior"),
        ("Pessoa Desenvolvedora Frontend Sênior", "senior"),
        # staff_plus
        ("Staff Engineer", "staff_plus"),
        ("Principal Engineer", "staff_plus"),
        ("Staff Software Engineer | iOS", "staff_plus"),
        ("Especialista em Software", "staff_plus"),
        # tech_lead
        ("Tech Lead", "tech_lead"),
        ("Frontend Tech Lead", "tech_lead"),
        ("Líder Técnico", "tech_lead"),
        ("Lead Engineer", "tech_lead"),
        # manager
        ("Engineering Manager", "manager"),
        ("Engineering Manager, Platform", "manager"),
        ("Gerente de Engenharia", "manager"),
        ("Coordenador de Tecnologia", "manager"),
        ("Head of Engineering", "manager"),
        # director_plus
        ("Director of Engineering", "director_plus"),
        ("VP of Engineering", "director_plus"),
        ("Diretor de Tecnologia", "director_plus"),
        ("CTO", "director_plus"),
        # unspecified
        ("Software Engineer", "unspecified"),
        ("Backend Developer", "unspecified"),
        ("Product Designer", "unspecified"),
    ],
)
def test_classify_level(title: str, expected: str) -> None:
    assert classify_level(title) in LEVELS
    assert classify_level(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # Role-shape outranks IC seniority.
        ("Senior Engineering Manager", "manager"),
        ("Senior Staff Engineer", "staff_plus"),
        ("Staff Engineering Manager", "manager"),
    ],
)
def test_level_precedence(title: str, expected: str) -> None:
    assert classify_level(title) == expected


def test_frontend_tech_lead_is_two_axes() -> None:
    # The taxonomy rationale: discipline × level are orthogonal.
    assert classify_discipline("Frontend Tech Lead") == "frontend"
    assert classify_level("Frontend Tech Lead") == "tech_lead"


# --- workplace (precedence + markers) --------------------------------------


def test_workplace_structured_field_wins() -> None:
    # Structured field beats conflicting free text.
    assert (
        classify_workplace("Remote", "New York, NY", "This is a hybrid role")
        == "remote"
    )
    assert (
        classify_workplace("On-site", "Remote-friendly", "work from home ok")
        == "onsite"
    )
    assert classify_workplace("Hybrid", "", "") == "hybrid"


@pytest.mark.parametrize(
    ("location", "description", "expected"),
    [
        ("Remote", "", "remote"),
        ("", "Trabalho remoto", "remote"),
        ("São Paulo (Híbrido)", "", "hybrid"),
        ("", "Presencial em Lisboa", "onsite"),
        ("New York, NY", "Great team, great pay", "unspecified"),
    ],
)
def test_workplace_from_text(location: str, description: str, expected: str) -> None:
    result = classify_workplace("", location, description)
    assert result in WORKPLACES
    assert result == expected


def test_workplace_unrecognized_structured_falls_to_text() -> None:
    assert classify_workplace("Flexible", "", "Fully remote position") == "remote"


def test_workplace_hybrid_beats_remote_in_text() -> None:
    assert classify_workplace("", "", "Hybrid role, remote up to 3 days") == "hybrid"


# --- remote geographic scope (anywhere / restricted / unspecified) ---------


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        # Explicit worldwide signal, nothing concrete left over -> anywhere.
        ("Anywhere in the World", "anywhere"),
        ("Home based - Worldwide", "anywhere"),
        ("Remote - Anywhere", "anywhere"),
        ("Remote (Global)", "anywhere"),
        ("Fully remote, work from anywhere", "anywhere"),
        # A concrete place named -> restricted, even alongside a worldwide-ish word.
        ("Remote, US", "restricted"),
        ("United States (Remote)", "restricted"),
        ("Remote-Friendly, United States", "restricted"),  # Anthropic (#38)
        ("New York, NY (HQ)", "restricted"),  # Ramp (#36)
        ("Home based - EMEA", "restricted"),
        ("Remote (Canada)", "restricted"),
        ("Remote - Australia", "restricted"),
        ("Anywhere in the US", "restricted"),  # worldwide word but a country residual
        # Remote with no locational signal either way -> unspecified.
        ("Remote", "unspecified"),
        ("", "unspecified"),
        ("N/A", "unspecified"),
    ],
)
def test_remote_scope_for_remote_roles(location: str, expected: str) -> None:
    result = classify_remote_scope("remote", location)
    assert result in REMOTE_SCOPES
    assert result == expected


@pytest.mark.parametrize("workplace", ["hybrid", "onsite", "unspecified", ""])
def test_remote_scope_only_applies_to_remote(workplace: str) -> None:
    # Scope is meaningless for a role that isn't remote — even a worldwide location.
    assert classify_remote_scope(workplace, "Anywhere in the World") == "unspecified"


def test_enrich_sets_remote_scope() -> None:
    anywhere = enrich(_job(location="Anywhere in the World", workplace_type="Remote"))
    assert anywhere.workplace == "remote"
    assert anywhere.remote_scope == "anywhere"
    restricted = enrich(_job(location="New York, NY (HQ)", workplace_type="Remote"))
    assert restricted.workplace == "remote"
    assert restricted.remote_scope == "restricted"
    onsite = enrich(_job(location="Berlin, Germany", workplace_type="On-site"))
    assert onsite.remote_scope == "unspecified"


# --- relocation / visa support ---------------------------------------------


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("We offer relocation assistance to all new hires.", "offered"),
        ("Relocation package and visa sponsorship included.", "offered"),
        ("Visa sponsorship available for international candidates.", "offered"),
        ("We provide relocation support; no visa sponsorship though.", "offered"),
        ("Oferecemos auxílio realocação para quem muda de cidade.", "offered"),
        # explicit refusals — negated before, negated after, and complete phrases
        ("There is no relocation assistance for this role.", "none"),
        ("Relocation assistance is not offered.", "none"),
        ("We do not offer relocation.", "none"),
        ("This position does not offer visa sponsorship.", "none"),
        ("Não oferecemos realocação para esta vaga.", "none"),
        # silence, and a mere requirement to relocate (not an employer offer)
        ("Build distributed systems in Rust.", "unspecified"),
        ("You must be willing to relocate to São Paulo.", "unspecified"),
    ],
)
def test_classify_relocation(description: str, expected: str) -> None:
    assert classify_relocation("Backend Engineer", description) == expected


def test_enrich_sets_relocation() -> None:
    offered = enrich(_job(description="Generous relocation package for the right hire."))
    assert offered.relocation == "offered"
    silent = enrich(_job(description="Build APIs."))
    assert silent.relocation == "unspecified"


# --- salary (four empirical formats + ambiguity) ---------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$152,800 — $259,200 USD", SalaryRange(152800.0, 259200.0, "USD", "year")),
        ("$189K – $330K", SalaryRange(189000.0, 330000.0, "USD", "year")),
        ("R$ 8.000 - R$ 12.000", SalaryRange(8000.0, 12000.0, "BRL", "month")),
        ("€60k–80k", SalaryRange(60000.0, 80000.0, "EUR", "year")),
    ],
)
def test_parse_salary_empirical_formats(raw: str, expected: SalaryRange) -> None:
    assert parse_salary(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "Competitive salary",
        "80,000 - 100,000",  # no currency anchor → ambiguous
        "A combinar",
        "R$ a combinar",  # currency but no number
        "USD",  # currency but no number
    ],
)
def test_parse_salary_ambiguous_returns_none(raw: str) -> None:
    assert parse_salary(raw) is None


def test_parse_salary_point_value() -> None:
    assert parse_salary("$120,000") == SalaryRange(120000.0, 120000.0, "USD", "year")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$50/hour", SalaryRange(50.0, 50.0, "USD", "hour")),
        ("$30/hr", SalaryRange(30.0, 30.0, "USD", "hour")),
        ("£45,000 per year", SalaryRange(45000.0, 45000.0, "GBP", "year")),
        ("R$ 120.000/ano", SalaryRange(120000.0, 120000.0, "BRL", "year")),
        ("R$ 5.000/mês", SalaryRange(5000.0, 5000.0, "BRL", "month")),
    ],
)
def test_parse_salary_period_markers(raw: str, expected: SalaryRange) -> None:
    assert parse_salary(raw) == expected


def test_parse_salary_period_from_description() -> None:
    # Explicit slash marker in the description disambiguates an otherwise
    # currency-defaulted period.
    assert parse_salary("$4,000", "paid /month") == SalaryRange(
        4000.0, 4000.0, "USD", "month"
    )


# --- salary parser v2: widened recognition (printed notation only) -----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # HTML entities from ATS content arrive verbatim in flattened strings.
        ("$165,000 &mdash; $190,000 USD", SalaryRange(165000.0, 190000.0, "USD", "year")),
        # Dollar-family prefixes ARE the printed currency — never USD-hijacked.
        ("CA$120,000 - CA$150,000", SalaryRange(120000.0, 150000.0, "CAD", "year")),
        ("A$150,000 - A$180,000", SalaryRange(150000.0, 180000.0, "AUD", "year")),
        # An explicit ISO code overrides a bare '$' symbol.
        ("$140,000 — $190,000 CAD", SalaryRange(140000.0, 190000.0, "CAD", "year")),
        # New ISO codes (fx_table has all 166 rates; recognition was the gap).
        ("PLN 26,500 to PLN 31,000", SalaryRange(26500.0, 31000.0, "PLN", "year")),
        ("CHF 120,000 - 140,000", SalaryRange(120000.0, 140000.0, "CHF", "year")),
        ("INR 2,500,000", SalaryRange(2500000.0, 2500000.0, "INR", "year")),
        # Shared-magnitude shorthand: the second number's printed magnitude is
        # the range's magnitude ('$80-100k' means 80k, not $80).
        ("$80-100k", SalaryRange(80000.0, 100000.0, "USD", "year")),
        ("EUR 80-110,000", SalaryRange(80000.0, 110000.0, "EUR", "year")),
        ("$1-2m", SalaryRange(1000000.0, 2000000.0, "USD", "year")),
        # The Ashby interval vocabulary flattened crawlers append.
        ("USD 77.88 - 86.54 / 1 HOUR", SalaryRange(77.88, 86.54, "USD", "hour")),
        ("S$8,000 - S$10,000 / 1 MONTH", SalaryRange(8000.0, 10000.0, "SGD", "month")),
    ],
)
def test_parse_salary_widened_formats(raw: str, expected: SalaryRange) -> None:
    assert parse_salary(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "$3B in annual sales",  # magnitude poison: salaries are never billions
        "$875,000,000 per month",  # absurd in a major currency → funding, not pay
        "€500 per day",  # day/week rates refuse — we have no honest factor
        "£450/day",
        "USD 0",  # zero is absence, not a figure
        "¥400,000/month",  # ambiguous symbol (JPY/CNY) with no code
        "kr 700,000",  # ambiguous 'kr' (SEK/NOK/DKK)
        "USD 100,000 / EUR 90,000",  # two explicit codes → which one?
        "$700/hour",  # contradictory: no major prints 700/hr — refuse, never re-period
        "USD 150,000 per month",  # contradictory month in a major
    ],
)
def test_parse_salary_refuses_contradictions_and_poison(raw: str) -> None:
    assert parse_salary(raw) is None


@pytest.mark.parametrize(
    ("description", "expected_period"),
    [
        # Slash markers glued to a word on the left are prose, not rates.
        ("ERP/HRIS systems experience", "year"),
        ("read https://modal.com/blog for context", "year"),
        ("People/HR Business Partner team", "year"),
        ("data/model/pipeline parallelism", "year"),
    ],
)
def test_parse_salary_description_markers_require_boundaries(
    description: str, expected_period: str
) -> None:
    parsed = parse_salary("$150,000 - $180,000 USD", description)
    assert parsed is not None
    assert parsed.period == expected_period


def test_parse_salary_description_rate_markers_still_fire() -> None:
    parsed = parse_salary("$45", "contract rate 45/hr, remote")
    assert parsed is not None
    assert parsed.period == "hour"


def test_salary_from_fields_normalizes_stated_period_vocab() -> None:
    # Ashby serves intervals as '1 MONTH'/'1 YEAR'/'1 HOUR'; crawlers pass them
    # as stated and the host maps them (§4.6 fix-once-fleet-wide). A stated
    # period we cannot map refuses the figure — defaulting it would mislabel.
    monthly = enrich(
        _job(
            salary_min=8000.0,
            salary_max=10000.0,
            salary_currency="SGD",
            salary_period="1 MONTH",
        )
    )
    assert monthly.salary is not None and monthly.salary.period == "month"
    yearly = enrich(
        _job(
            salary_min=100000.0,
            salary_max=120000.0,
            salary_currency="USD",
            salary_period="Annual",
        )
    )
    assert yearly.salary is not None and yearly.salary.period == "year"
    odd = enrich(
        _job(
            salary_min=1000.0,
            salary_max=2000.0,
            salary_currency="USD",
            salary_period="biweekly",
        )
    )
    assert odd.salary is None


def test_salary_from_fields_refuses_zero_and_negative_bounds() -> None:
    # devjobsscanner-style APIs send salaryMin=0 for "unstated" — zero is
    # absence, never a floor.
    assert enrich(_job(salary_min=0.0, salary_max=120000.0)).salary is None
    assert enrich(_job(salary_min=-5.0, salary_max=-1.0)).salary is None


def test_enrich_sets_salary_provenance() -> None:
    structured = enrich(
        _job(
            salary_min=100000.0,
            salary_max=120000.0,
            salary_currency="USD",
            salary_period="year",
        )
    )
    assert structured.salary_origin == "structured"
    text = enrich(_job(salary="$150k - $200k"))
    assert text.salary_origin == "text"
    assert enrich(_job()).salary_origin is None


def test_salary_parser_version_pinned() -> None:
    # Bumping SALARY_PARSER_VERSION replays the store-open repair sweep (and the
    # salary_raw re-parse) — any parse_salary behavior change needs a bump.
    from jobcrawler.runtime.normalize import SALARY_PARSER_VERSION

    assert SALARY_PARSER_VERSION == 1


# --- enrich (composition) --------------------------------------------------


def _job(**overrides: object) -> Job:
    fields: dict[str, object] = {
        "url": "https://x.com/jobs/1",
        "title": "Senior Backend Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": "Build APIs.",
        "source": "acme",
    }
    fields.update(overrides)
    return Job.model_validate(fields)


def test_enrich_composes_axes_and_parsed_salary() -> None:
    e = enrich(_job(salary="$100,000 - $150,000"))
    assert e.discipline == "backend"
    assert e.level == "senior"
    assert e.workplace == "remote"
    assert e.salary == SalaryRange(100000.0, 150000.0, "USD", "year")


def test_enrich_prefers_structured_salary_fields() -> None:
    # Crawler set structured compensation → used verbatim, verbatim text ignored.
    e = enrich(
        _job(
            salary="ignored text",
            salary_min=90000.0,
            salary_max=120000.0,
            salary_currency="EUR",
            salary_period="year",
        )
    )
    assert e.salary == SalaryRange(90000.0, 120000.0, "EUR", "year")


def test_enrich_salary_none_when_absent() -> None:
    assert enrich(_job()).salary is None


def test_enrich_workplace_prefers_structured_field() -> None:
    e = enrich(_job(location="Berlin", workplace_type="Hybrid"))
    assert e.workplace == "hybrid"


# --- company dedup key (§16) -----------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        # surface variants of one company collapse to a single key
        ("Acme", "acme"),
        ("ACME", "acme"),
        ("Acme, Inc.", "acme"),
        ("Acme Inc", "acme"),
        ("Acme LLC", "acme"),
        ("Acme Ltd.", "acme"),
        ("Acme GmbH", "acme"),
        ("The Acme Company", "acme"),
        ("Acme  Labs", "acmelabs"),
        # stacked suffixes cascade off
        ("Acme Corp Ltd", "acme"),
        # accents fold (pt-BR)
        ("Padrão Tecnologia", "padraotecnologia"),
        # a bare suffix is a real (if odd) name, never stripped to empty
        ("Co", "co"),
        ("Group", "group"),
        # nothing to key on
        ("", ""),
        ("   ", ""),
        (",.-", ""),
    ],
)
def test_canonical_company(name: str, expected: str) -> None:
    assert canonical_company(name) == expected


def test_canonical_company_does_not_merge_distinct_names() -> None:
    # "Meta" and "Meta Materials" are different companies — the key must keep them apart.
    assert canonical_company("Meta") != canonical_company("Meta Materials")


def test_canonical_company_keeps_mid_name_suffix_token() -> None:
    # "Inc" only drops as a *trailing* token; a name that merely contains it survives.
    assert canonical_company("Incredible Health") == "incrediblehealth"


# --- location normalization (§4.6) -----------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # country synonyms across language + abbreviation collapse to one form
        ("são paulo, brasil", "São Paulo, Brazil"),
        ("Remote, EUA", "Remote, United States"),
        ("Austin, US", "Austin, United States"),
        ("New York, USA", "New York, United States"),
        ("London, UK", "London, United Kingdom"),
        # consistent casing: all-lower and ALL-UPPER both title-case
        ("new york", "New York"),
        ("NEW YORK", "New York"),
        # region / state codes stay upper as a whole part
        ("Remote, EMEA", "Remote, EMEA"),
        ("Austin, TX", "Austin, TX"),
        # pt-BR / EN connectives stay lowercase inside a part
        ("rio de janeiro", "Rio de Janeiro"),
        ("isle of man", "Isle of Man"),
        # remote phrasings normalize (incl. pt-BR)
        ("Remoto", "Remote"),
        ("fully remote", "Remote"),
        # separators unify to commas; a spaced dash is a separator
        ("Remote - US", "Remote, United States"),
        ("Berlin | Germany", "Berlin, Germany"),
        ("São Paulo / Brasil", "São Paulo, Brazil"),
        # intra-word hyphen is NOT a separator
        ("Winston-Salem, NC", "Winston-Salem, NC"),
        # deliberate internal caps are preserved
        ("McLean, VA", "McLean, VA"),
        # duplicate parts collapse (raw + synonym alias too)
        ("Remote, Remote", "Remote"),
        ("United States, US", "United States"),
        # whitespace is cleaned; empty in → empty out
        ("  são   paulo ,  brasil ", "São Paulo, Brazil"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_location(raw: str, expected: str) -> None:
    assert normalize_location(raw) == expected


def test_normalize_location_is_stable_under_reapplication() -> None:
    # Its own output is already canonical — re-normalizing is a no-op (idempotent),
    # so recomputing on every crawl never drifts.
    for raw in ("são paulo, brasil", "Remote - US", "new york, ny"):
        once = normalize_location(raw)
        assert normalize_location(once) == once


def test_normalize_location_preserves_raw_for_content_hash() -> None:
    # It NEVER mutates the caller's string — the raw ``location`` still feeds
    # ``content_hash`` verbatim, so normalization can't silently re-score the backlog.
    raw = "são paulo, brasil"
    normalize_location(raw)
    assert raw == "são paulo, brasil"


# --- annualized-USD salary band (§4.6) -------------------------------------


@pytest.mark.parametrize(
    ("salary_min", "salary_max", "currency", "period", "expected"),
    [
        # USD annual is the identity
        (100_000.0, 150_000.0, "USD", "year", (100_000.0, 150_000.0)),
        # monthly annualizes ×12; BRL converts at 0.18
        (10_000.0, 20_000.0, "BRL", "month", (21_600.0, 43_200.0)),
        # EUR/GBP annual conversion
        (80_000.0, 100_000.0, "EUR", "year", (86_400.0, 108_000.0)),
        (80_000.0, 100_000.0, "GBP", "year", (101_600.0, 127_000.0)),
        # hourly annualizes ×2080
        (50.0, 60.0, "USD", "hour", (104_000.0, 124_800.0)),
        # case/whitespace-insensitive on both keys
        (100_000.0, 100_000.0, " usd ", " Year ", (100_000.0, 100_000.0)),
        # an open-ended bound stays open-ended
        (None, 120_000.0, "USD", "year", (None, 120_000.0)),
        # unknown currency or period → no half-converted figure
        (100_000.0, 120_000.0, "JPY", "year", (None, None)),
        (100_000.0, 120_000.0, "USD", "decade", (None, None)),
        (100_000.0, 120_000.0, None, None, (None, None)),
    ],
)
def test_usd_annual_band(
    salary_min: float | None,
    salary_max: float | None,
    currency: str | None,
    period: str | None,
    expected: tuple[float | None, float | None],
) -> None:
    lo, hi = usd_annual_band(salary_min, salary_max, currency, period)
    exp_lo, exp_hi = expected
    assert lo == pytest.approx(exp_lo) if exp_lo is not None else lo is None
    assert hi == pytest.approx(exp_hi) if exp_hi is not None else hi is None


def test_usd_annual_band_orders_currencies_coherently() -> None:
    # The whole point: a BRL-monthly range and a USD-annual range become comparable.
    # 25k BRL/mo ≈ $54k/yr sits below a $120k/yr USD role once both are annualized-USD.
    brl_lo, brl_hi = usd_annual_band(20_000.0, 25_000.0, "BRL", "month")
    usd_lo, usd_hi = usd_annual_band(120_000.0, 140_000.0, "USD", "year")
    assert brl_hi is not None and usd_lo is not None
    assert brl_hi < usd_lo


def test_usd_annual_band_injected_rates_override_the_static_table() -> None:
    # A live rate handed in as ``fx_rates`` is used instead of the static FX_TO_USD.
    lo, hi = usd_annual_band(
        80_000.0, 100_000.0, "EUR", "year", fx_rates={"EUR": 1.20}
    )
    assert (lo, hi) == pytest.approx((96_000.0, 120_000.0))


def test_usd_annual_band_injected_rates_extend_currency_coverage() -> None:
    # A currency the static table can't reach is unstated by default...
    assert usd_annual_band(5_000_000.0, None, "JPY", "year") == (None, None)
    # ...but converts once a live rate for it is supplied.
    lo, _ = usd_annual_band(5_000_000.0, None, "JPY", "year", fx_rates={"JPY": 0.0064})
    assert lo == pytest.approx(32_000.0)
