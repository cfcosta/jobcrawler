"""Deterministic host-side enrichment of a crawled :class:`Job` (§4.6/§8).

A crawler yields *raw* postings (title, description, raw ``workplace_type``,
verbatim ``salary`` text). Turning those into the closed taxonomies the profile
matcher and status surfaces reason over — a two-axis (discipline × level)
classification, a workplace bucket, and a parsed salary range — happens **here**,
once, deterministically, and **never with an LLM**.

Why one shared module instead of per-crawler logic (the load-bearing invariant):

  * **No LLM in the crawl loop (§4.6/§8).** Committed crawlers are plain Python;
    the only new LLM surface is the host-side profile matcher (:mod:`jobcrawler.
    match`), which runs *outside* the sandbox, batched and cached. Classification
    must therefore be a pure function of the text — cheap, offline, and free to
    run on every posting every crawl.
  * **Fix once, fixed fleet-wide.** Because enrichment is host-side and shared,
    a classification bug (a mis-bucketed title, a new pt-BR seniority word) is
    fixed in *this* file and every site benefits immediately — no crawler is
    regenerated, no §4.11 optimize run is needed. If each generated crawler
    embedded its own ``classify_*`` heuristics we could never fix them centrally.
  * **Determinism = testability.** Pure in → out means the whole taxonomy is
    pinned by unit tests (EN + pt-BR), so a refactor that changes a boundary is
    caught, not silently shipped.

**Two-axis taxonomy.** Discipline (*what kind of engineering*) and level (*how
senior / what role-shape*) are orthogonal: ``Frontend Tech Lead`` is
``(frontend, tech_lead)``; ``Staff Backend Engineer`` is ``(backend,
staff_plus)``; ``Engineering Manager`` is ``(eng_other, manager)``. Splitting
them lets the profile express "senior-or-above backend/frontend" without a
combinatorial enum. Role-shape words (manager/director/tech-lead) live on the
*level* axis, not the discipline axis.

**Recall-safe by construction.** ``non_eng`` and a *known* specific discipline
are the only confident signals the §6 prefilter drops on; an unrecognized title
resolves to ``eng_other`` (discipline) / ``unspecified`` (level) so it *passes*
to the matcher rather than being silently dropped. We only say "not this" when a
keyword makes us sure. Salary parsing is likewise conservative: ``None`` unless a
currency anchor and a number are both present — we never invent a figure the
posting did not print (§4.6).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from jobcrawler.runtime.contract import Job

# --- closed taxonomies (the enum values other modules key on) --------------
# Exported as frozensets so the profile loader (§5) can validate a PROFILE.md's
# preference values against them (unknown entry → ValueError). The catch-alls
# ('eng_other'/'non_eng'/'unspecified') are part of the enum a classifier may
# emit; the profile layer decides which are meaningful as *preferences*.

DISCIPLINES: frozenset[str] = frozenset(
    {
        "backend",
        "frontend",
        "fullstack",
        "mobile",
        "devops_sre",
        "data",
        "ml_ai",
        "security",
        "qa",
        "embedded",
        "eng_other",
        "non_eng",
    }
)
LEVELS: frozenset[str] = frozenset(
    {
        "intern",
        "junior",
        "mid",
        "senior",
        "staff_plus",
        "tech_lead",
        "manager",
        "director_plus",
        "unspecified",
    }
)
WORKPLACES: frozenset[str] = frozenset({"remote", "hybrid", "onsite", "unspecified"})


# --- value objects ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SalaryRange:
    """A parsed compensation band. ``min``/``max`` are equal for a point value."""

    min: float
    max: float
    currency: str  # ISO-ish code: 'USD' | 'EUR' | 'GBP' | 'BRL'
    period: str  # 'year' | 'month' | 'hour'


@dataclass(frozen=True, slots=True)
class EnrichedJob:
    """A :class:`Job` plus its host-side, deterministic enrichment (§4.6)."""

    job: Job
    discipline: str
    level: str
    workplace: str
    salary: SalaryRange | None


# --- text matching primitives ----------------------------------------------


def _fold(text: str) -> str:
    """Lowercase + strip diacritics so pt-BR matches accent-insensitively.

    'Sênior'→'senior', 'Estágio'→'estagio', 'Híbrido'→'hibrido'. Currency
    symbols ('$', '€', '£') and 'R$' survive NFKD untouched, so the same fold
    feeds salary parsing. All keyword lists below are written in this folded
    (unaccented, lowercase) form.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.lower()


_PAT_CACHE: dict[str, re.Pattern[str]] = {}


def _pat(needle: str) -> re.Pattern[str]:
    """Compile+cache a token-boundary matcher for ``needle`` (folded text).

    Boundaries are alphanumeric-anchored so short tokens don't leak: ``\\bios\\b``
    hits 'ios' in 'ios engineer' but not 'various'; ``\\bstaff\\b`` misses
    'staffing'; ``\\bengineer\\b`` deliberately misses 'engineering' (both are
    listed where needed).
    """
    pat = _PAT_CACHE.get(needle)
    if pat is None:
        pat = re.compile(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])")
        _PAT_CACHE[needle] = pat
    return pat


def _has(text: str, needles: tuple[str, ...]) -> bool:
    return any(_pat(n).search(text) for n in needles)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    """Plain substring test — for markers whose neighbours are non-word chars
    ('/hr', '/ano', 'per year') that token boundaries would reject."""
    return any(n in text for n in needles)


# --- discipline classification ---------------------------------------------
# Specific disciplines are matched first, ordered so a more-specific label wins
# over a substring it contains: ml_ai before data ('data scientist' is ml_ai);
# mobile before frontend ('react native' before bare 'react'); the big three
# (fullstack/frontend/backend) last. Keyword sets are kept *precise* (a wrong
# specific discipline can wrongly drop a job at prefilter); the generic ENG /
# NON_ENG markers below are the recall-safe fallbacks.

_MOBILE = (
    "mobile",
    "ios",
    "android",
    "react native",
    "flutter",
    "swiftui",
    "app engineer",
    "android engineer",
    "ios engineer",
    "desenvolvedor mobile",
)
_ML_AI = (
    "machine learning",
    "deep learning",
    "ml engineer",
    "mlops",
    "ml ops",
    "ai engineer",
    "artificial intelligence",
    "nlp",
    "computer vision",
    "llm",
    "generative ai",
    "data scientist",
    "aprendizado de maquina",
    "inteligencia artificial",
    "cientista de dados",
)
_DATA = (
    "data engineer",
    "data analyst",
    "data analytics",
    "analytics engineer",
    "business intelligence",
    "data platform",
    "data warehouse",
    "etl",
    "big data",
    "engenheiro de dados",
    "analista de dados",
    "engenharia de dados",
)
_DEVOPS = (
    "devops",
    "dev ops",
    "sre",
    "site reliability",
    "reliability engineer",
    "platform engineer",
    "platform engineering",
    "infrastructure engineer",
    "cloud engineer",
    "kubernetes",
    "observability",
    "infraestrutura",
    "engenheiro de infraestrutura",
)
_SECURITY = (
    "security engineer",
    "security analyst",
    "application security",
    "appsec",
    "infosec",
    "information security",
    "cybersecurity",
    "cyber security",
    "penetration test",
    "pentest",
    "seguranca da informacao",
    "ciberseguranca",
)
_QA = (
    "qa",
    "qa engineer",
    "quality assurance",
    "quality engineer",
    "sdet",
    "test engineer",
    "test automation",
    "tester",
    "analista de testes",
    "engenheiro de testes",
    "garantia de qualidade",
)
_EMBEDDED = (
    "embedded",
    "firmware",
    "fpga",
    "rtos",
    "device driver",
    "microcontroller",
    "sistemas embarcados",
    "embarcado",
)
_FULLSTACK = ("full stack", "full-stack", "fullstack", "desenvolvedor full stack")
_FRONTEND = (
    "frontend",
    "front end",
    "front-end",
    "ui engineer",
    "ui developer",
    "react",
    "angular",
    "vue",
    "vuejs",
    "svelte",
    "ember",
)
_BACKEND = (
    "backend",
    "back end",
    "back-end",
    "server-side",
    "server side",
    "api engineer",
    "api developer",
    "nodejs",
    "node.js",
)

# Ordered: first specific match wins. See ordering rationale above.
_DISCIPLINE_ORDER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ml_ai", _ML_AI),
    ("data", _DATA),
    ("devops_sre", _DEVOPS),
    ("security", _SECURITY),
    ("qa", _QA),
    ("embedded", _EMBEDDED),
    ("mobile", _MOBILE),
    ("fullstack", _FULLSTACK),
    ("frontend", _FRONTEND),
    ("backend", _BACKEND),
)

# Generic "this is (some kind of) engineering" markers → eng_other when no
# specific discipline matched. Note 'engineer' (\b) does NOT match 'engineering',
# so both are listed. These run *before* NON_ENG so 'Sales Engineer' /
# 'Security Guard'-adjacent eng titles resolve eng_other (recall-safe), not
# non_eng.
_ENG_MARKERS = (
    "engineer",
    "engineering",
    "developer",
    "programmer",
    "software",
    "swe",
    "sde",
    "desenvolvedor",
    "desenvolvedora",
    "engenheiro",
    "engenheira",
    "engenharia",
    "programador",
    "programadora",
    "architect",
    "arquiteto",
    "sysadmin",
    "devops",
    "tech lead",
    "technical lead",
    "lead engineer",
    "engineering lead",
)

# Confident non-engineering signals. Only consulted when no eng marker fired, so
# these are the sole titles we actively drop (§6 prefilter). Kept to common,
# unambiguous roles — anything unlisted falls through to eng_other (→ matcher).
_NON_ENG_MARKERS = (
    "designer",
    "ux",
    "product manager",
    "product owner",
    "gerente de produto",
    "product management",
    "sales",
    "vendas",
    "comercial",
    "account executive",
    "account manager",
    "marketing",
    "seo",
    "recruiter",
    "recruiting",
    "recruitment",
    "talent acquisition",
    "sourcer",
    "recrutador",
    "recrutamento",
    "human resources",
    "recursos humanos",
    "people operations",
    "people partner",
    "payroll",
    "finance",
    "financial",
    "financeiro",
    "accountant",
    "accounting",
    "contador",
    "contabilidade",
    "bookkeeper",
    "legal",
    "counsel",
    "attorney",
    "juridico",
    "advogado",
    "customer success",
    "customer support",
    "customer service",
    "atendimento",
    "suporte",
    "content writer",
    "copywriter",
    "technical writer",
    "redator",
    "community manager",
    "social media",
    "business analyst",
    "project manager",
    "program manager",
    "scrum master",
    "agile coach",
    "office manager",
    "executive assistant",
    "administrative",
    "nurse",
    "teacher",
    "driver",
)


def classify_discipline(title: str, description: str = "") -> str:
    """Map a posting to its engineering discipline (or ``non_eng``) — §4.6.

    Title-first (the strongest, cleanest signal); the description is consulted
    only when the title carries *no* discipline, eng, or non-eng signal at all
    (an opaque title like a bare team name), so a JD that merely *mentions*
    another stack can't override an explicit title.
    """
    t = _fold(title)
    result = _discipline_of(t)
    if result is not None:
        return result
    # Title was fully opaque — fall back to the description as the only signal.
    result = _discipline_of(_fold(description))
    return result if result is not None else "eng_other"


def _discipline_of(text: str) -> str | None:
    for label, needles in _DISCIPLINE_ORDER:
        if _has(text, needles):
            return label
    if _has(text, _ENG_MARKERS):
        return "eng_other"
    if _has(text, _NON_ENG_MARKERS):
        return "non_eng"
    return None


# --- level classification --------------------------------------------------
# Precedence, highest-authority first: role-shape (director/manager/tech-lead)
# outranks IC seniority (staff/senior/mid/junior/intern). So 'Senior Engineering
# Manager' → manager, 'Senior Staff Engineer' → staff_plus. A title bearing two
# signals resolves to the one listed earliest here.

_DIRECTOR = (
    "director",
    "vp",
    "svp",
    "evp",
    "vice president",
    "vice-president",
    "chief",
    "cto",
    "cio",
    "ceo",
    "coo",
    "diretor",
    "diretora",
    "vice presidente",
    "vice-presidente",
)
_MANAGER = (
    "manager",
    "engineering manager",
    "head of",
    "coordinator",
    "coordenador",
    "coordenadora",
    "gerente",
)
_TECH_LEAD = (
    "tech lead",
    "technical lead",
    "tech-lead",
    "lead engineer",
    "engineering lead",
    "lider tecnico",
)
_STAFF_PLUS = (
    "staff",
    "principal",
    "distinguished",
    "especialista",
)
_SENIOR = ("senior", "sr", "snr")
_MID = (
    "mid",
    "mid-level",
    "midlevel",
    "mid level",
    "intermediate",
    "middle",
    "pleno",
    "plena",
)
_JUNIOR = ("junior", "jr", "entry level", "entry-level", "associate")
_INTERN = (
    "intern",
    "internship",
    "trainee",
    "estagio",
    "estagiario",
    "estagiaria",
    "aprendiz",
    "co-op",
    "coop",
)

_LEVEL_ORDER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("director_plus", _DIRECTOR),
    ("manager", _MANAGER),
    ("tech_lead", _TECH_LEAD),
    ("staff_plus", _STAFF_PLUS),
    ("senior", _SENIOR),
    ("mid", _MID),
    ("junior", _JUNIOR),
    ("intern", _INTERN),
)

# Description fallback deliberately omits the role-shape tiers: a JD routinely
# says "reports to the Director" / "partner with Engineering Managers" about
# *other* roles, so mining it for those would mislabel. IC seniority words are
# far safer, and precedence still lets 'senior' beat a later 'junior' mention.
_LEVEL_IC_ORDER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("staff_plus", _STAFF_PLUS),
    ("senior", _SENIOR),
    ("mid", _MID),
    ("junior", _JUNIOR),
    ("intern", _INTERN),
)


def classify_level(title: str, description: str = "") -> str:
    """Map a posting to its seniority / role-shape level (§4.6).

    Title-first with full precedence; if the title states no level, fall back to
    IC-seniority words in the description only (role-shape words there usually
    describe reporting lines, not the role itself).
    """
    t = _fold(title)
    for label, needles in _LEVEL_ORDER:
        if _has(t, needles):
            return label
    b = _fold(description)
    for label, needles in _LEVEL_IC_ORDER:
        if _has(b, needles):
            return label
    return "unspecified"


# --- workplace classification ----------------------------------------------
# The structured field wins outright; only when it is empty/unrecognized do we
# read markers out of location+description. Within either source, hybrid is
# tested first (a 'hybrid' posting mentioning 'remote' days is hybrid, not
# remote), then remote, then onsite.

_WP_HYBRID = ("hybrid", "hibrido", "hybrid remote")
_WP_REMOTE = (
    "remote",
    "remoto",
    "work from home",
    "wfh",
    "fully remote",
    "remote-first",
    "remote first",
    "trabalho remoto",
)
_WP_ONSITE = (
    "onsite",
    "on-site",
    "on site",
    "in-office",
    "in office",
    "in-person",
    "in person",
    "on premises",
    "on-premise",
    "presencial",
)


def _match_workplace(text: str) -> str | None:
    if _has(text, _WP_HYBRID):
        return "hybrid"
    if _has(text, _WP_REMOTE):
        return "remote"
    if _has(text, _WP_ONSITE):
        return "onsite"
    return None


def classify_workplace(workplace_type: str, location: str, description: str) -> str:
    """Bucket a posting's work arrangement, structured field taking precedence."""
    structured = _match_workplace(_fold(workplace_type or ""))
    if structured is not None:
        return structured
    text = _fold((location or "") + " " + (description or ""))
    return _match_workplace(text) or "unspecified"


# --- company dedup key -----------------------------------------------------
# Legal/entity suffixes dropped when collapsing a hiring company to a grouping key,
# in folded (lowercase, unaccented) form. Matched only as a *trailing* token, so an
# "Inc" sitting mid-name survives; the cascade handles stacked suffixes ("Acme Corp Ltd").
_COMPANY_SUFFIXES: frozenset[str] = frozenset(
    {
        "inc", "incorporated", "llc", "ltd", "limited", "llp", "plc", "co", "corp",
        "corporation", "company", "holdings", "group", "gmbh", "ag", "sa", "sas",
        "bv", "nv", "oy", "ab", "as", "pty", "pte", "srl", "spa", "kk", "kft", "ltda",
    }
)


def canonical_company(name: str) -> str:
    """A stable dedup key for a hiring company name (§16), or ``""`` if nothing to key on.

    Folds case/accents, drops a leading "the" and any trailing legal-entity suffix
    (Inc / LLC / Ltd / GmbH / …), and removes punctuation and whitespace, so
    ``"Acme, Inc."``, ``"ACME"`` and ``"The Acme Company"`` collapse to one key
    (``"acme"``). Used only for *grouping* — dedup and the "already applied at this
    company" top-match rule (§16); the verbatim ``company`` is what's displayed.
    Deliberately conservative: it normalizes surface variants of one name, and never
    tries to merge genuinely different names.
    """
    tokens = [t for t in re.split(r"[^a-z0-9]+", _fold(name)) if t]
    if len(tokens) > 1 and tokens[0] == "the":
        tokens = tokens[1:]
    while len(tokens) > 1 and tokens[-1] in _COMPANY_SUFFIXES:
        tokens.pop()
    return "".join(tokens)


# --- salary parsing --------------------------------------------------------
# Conservative text→range extraction (§4.6). A currency anchor is *mandatory*: a
# bare '80,000 - 100,000' is ambiguous (which currency?) and returns None — we
# never guess. Numbers come only from ``raw`` (the explicit salary field), never
# mined from the free-text description, so a JD's '$5M in funding' can't leak in.

_CUR_NUMBER = re.compile(r"(\d[\d.,]*\d|\d) ?([km])?")
_RANGE_SEPS = ("-", "–", "—", "−", " to ", " a ", " ate ", "~")

# Period markers. Explicit slash / 'per X' / 'por X' forms are safe to read out
# of a description; the bare words (monthly/annual/hourly) are only trusted in
# the raw salary field itself.
_HOUR = ("per hour", "/hour", "/hr", "hourly", "por hora", "/hora")
_MONTH = (
    "per month",
    "/month",
    "/mo",
    "monthly",
    "por mes",
    "/mes",
    "mensal",
    "a month",
)
_YEAR = (
    "per year",
    "per annum",
    "/year",
    "/yr",
    "yearly",
    "annually",
    "annual",
    "por ano",
    "/ano",
    "anual",
    "a year",
    "p.a.",
)
_SAFE_HOUR = ("per hour", "/hour", "/hr", "por hora", "/hora")
_SAFE_MONTH = ("per month", "/month", "/mo", "por mes", "/mes")
_SAFE_YEAR = ("per year", "per annum", "/year", "/yr", "por ano", "/ano")


def _detect_currency(text: str) -> str | None:
    """The currency anchor, if any. 'R$' is tested before bare '$' (it contains
    one); a code ('usd') and its symbol are equivalent."""
    if "r$" in text or _has(text, ("brl", "reais")):
        return "BRL"
    if "€" in text or _has(text, ("eur", "euro", "euros")):
        return "EUR"
    if "£" in text or _has(text, ("gbp",)):
        return "GBP"
    if "$" in text or _has(text, ("usd",)):
        return "USD"
    return None


def _strip_separators(token: str) -> float | None:
    """Parse a numeric token, resolving ',' vs '.' as thousands or decimal.

    Groups-of-three ('152,800', '8.000') are thousands; a trailing 1-2 digit
    group is a decimal; a mixed token keeps its *last* separator as the decimal
    point (US '1,234.56' and BR '1.234,56' both → 1234.56).
    """
    s = token.strip()
    has_comma, has_dot = "," in s, "." in s
    if has_comma and has_dot:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_comma:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")
        elif re.fullmatch(r"\d+,\d{1,2}", s):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_dot:
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")
        elif not re.fullmatch(r"\d+\.\d{1,2}", s):
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _extract_numbers(text: str) -> list[float]:
    out: list[float] = []
    for match in _CUR_NUMBER.finditer(text):
        value = _strip_separators(match.group(1))
        if value is None:
            continue
        suffix = match.group(2)
        if suffix == "k":
            value *= 1000
        elif suffix == "m":
            value *= 1_000_000
        out.append(value)
    return out


def _detect_period(
    text: str, hour: tuple[str, ...], month: tuple[str, ...], year: tuple[str, ...]
) -> str | None:
    if _contains_any(text, hour):
        return "hour"
    if _contains_any(text, month):
        return "month"
    if _contains_any(text, year):
        return "year"
    return None


def _default_period(currency: str) -> str:
    """BRL postings quote monthly by convention; USD/EUR/GBP quote yearly."""
    return "month" if currency == "BRL" else "year"


def parse_salary(raw: str, description: str = "") -> SalaryRange | None:
    """Extract a :class:`SalaryRange` from verbatim salary text, or ``None``.

    Conservative (§4.6): returns ``None`` unless *both* a currency anchor and at
    least one number are present in ``raw``. A range needs two numbers separated
    by a dash / 'to' / 'até'; a lone number yields a point band (min == max).
    Period is taken from an explicit marker in ``raw``, else a slash/'per' marker
    in ``description``, else the currency's convention.
    """
    text = _fold(raw or "")
    currency = _detect_currency(text)
    if currency is None:
        return None
    numbers = _extract_numbers(text)
    if not numbers:
        return None
    if len(numbers) >= 2 and _contains_any(text, _RANGE_SEPS):
        lo, hi = sorted(numbers[:2])
    else:
        lo = hi = numbers[0]
    period = (
        _detect_period(text, _HOUR, _MONTH, _YEAR)
        or _detect_period(_fold(description or ""), _SAFE_HOUR, _SAFE_MONTH, _SAFE_YEAR)
        or _default_period(currency)
    )
    return SalaryRange(lo, hi, currency, period)


# --- top-level enrichment --------------------------------------------------


def _salary_from_fields(job: Job) -> SalaryRange | None:
    """Build a range from the crawler's *structured* salary fields, if it set
    them (the source exposed structured compensation). ``None`` → fall back to
    parsing the verbatim ``salary`` text."""
    if job.salary_min is None and job.salary_max is None:
        return None
    lo = job.salary_min if job.salary_min is not None else job.salary_max
    hi = job.salary_max if job.salary_max is not None else job.salary_min
    if lo is None or hi is None:
        return None
    if lo > hi:
        lo, hi = hi, lo
    currency = job.salary_currency or ""
    period = job.salary_period or _default_period(currency)
    return SalaryRange(float(lo), float(hi), currency, period)


def enrich(job: Job) -> EnrichedJob:
    """Attach the deterministic (discipline, level, workplace, salary) enrichment.

    Salary prefers the crawler's structured fields (set only when the source
    exposed them as data); otherwise it parses the verbatim ``salary`` text —
    both host-side, never an LLM (§4.6/§8).
    """
    salary = _salary_from_fields(job)
    if salary is None:
        salary = parse_salary(job.salary or "", job.description or "")
    return EnrichedJob(
        job=job,
        discipline=classify_discipline(job.title, job.description),
        level=classify_level(job.title, job.description),
        workplace=classify_workplace(
            job.workplace_type or "", job.location, job.description
        ),
        salary=salary,
    )
