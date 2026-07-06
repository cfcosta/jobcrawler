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
    WORKPLACES,
    SalaryRange,
    canonical_company,
    classify_discipline,
    classify_level,
    classify_workplace,
    enrich,
    parse_salary,
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
