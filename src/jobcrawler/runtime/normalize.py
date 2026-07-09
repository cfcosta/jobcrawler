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

import html
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
# Geographic scope of a remote role (§4.6). Meaningful only when workplace is remote.
REMOTE_SCOPES: frozenset[str] = frozenset({"anywhere", "restricted", "unspecified"})


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
    remote_scope: str
    relocation: str
    salary: SalaryRange | None
    # Where ``salary`` came from: 'structured' (source data fields) | 'text'
    # (host parse of the printed string) | None (no salary). The runner persists
    # it as ``salary_source``; the §4.7 LLM backfill stamps its own 'llm'.
    salary_origin: str | None = None


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


# --- remote geographic scope -----------------------------------------------
# ``workplace='remote'`` says a role is remote but NOT *where you may sit* — that
# scope lives in the freeform ``location`` ("Anywhere in the World" vs "Remote,
# US" vs "New York, NY (HQ)"). ``remote_scope`` recovers the coarse distinction so
# a US-only remote no longer reads as bare "Remote" and a "remote anywhere" filter
# becomes possible. Deliberately conservative and asymmetric: only an explicit
# worldwide signal with NO concrete place left over earns ``anywhere``; ANY residual
# place token (a country/region/state/city) is ``restricted``; a bare "Remote" with
# no locational signal either way is ``unspecified``. We never read the description
# — it names cities constantly and would manufacture false ``anywhere``/``restricted``.

# Worldwide signals. Multi-word entries rely on the folded ``location`` keeping its
# spaces; their component tokens also live in ``_SCOPE_FILLER`` so they don't count
# as a leftover place.
_SCOPE_GLOBAL = (
    "anywhere",
    "worldwide",
    "world wide",
    "world",
    "global",
    "globally",
    "everywhere",
    "international",
    "location independent",
    "any location",
    "any country",
    "nomad",
)
# Folded tokens that are NOT a geographic restriction: remote-arrangement words, the
# worldwide-signal tokens (so they don't read as a place), generic noise, and
# structural stopwords. Deliberately EXCLUDES every place token — "us", "usa", "uk",
# "eu", "emea", "apac", "united", "states", "york", … must survive as residual so a
# scoped remote lands in ``restricted``.
_SCOPE_FILLER: frozenset[str] = frozenset(
    {
        # remote arrangement
        "remote", "remoto", "remotely", "fully", "wfh", "telecommute", "telework",
        "distributed", "virtual", "home", "based", "work", "office", "hq",
        "headquarters", "hybrid", "onsite",
        # worldwide-signal tokens (kept out of the residual place set)
        "anywhere", "worldwide", "world", "wide", "global", "globally", "everywhere",
        "international", "location", "independent", "any", "country", "nomad",
        "planet", "earth",
        # generic non-place noise
        "friendly", "only", "position", "positions", "role", "roles", "job", "jobs",
        "opportunity", "opportunities", "team", "teams", "flexible", "optional",
        "preferred", "locations", "available", "tbd", "na",
        # structural stopwords ("us"/"eu" are places, NOT stopwords — excluded here)
        "in", "the", "an", "of", "or", "and", "on", "at", "to", "for", "from", "with",
        "within", "across", "is", "be", "our", "your", "a", "n",
    }
)


def classify_remote_scope(workplace: str, location: str) -> str:
    """Coarse geographic scope of a remote role: anywhere / restricted / unspecified.

    Only meaningful for a ``remote`` ``workplace`` (hybrid/onsite carry their city in
    ``location`` already and return ``unspecified``). Strips remote/worldwide filler
    from the folded ``location``; a non-empty residual names a concrete place →
    ``restricted``; an empty residual with a worldwide signal → ``anywhere``; empty and
    silent → ``unspecified``. Conservative by construction: it only ever *demotes* a
    remote from an assumed "anywhere", never invents a worldwide claim the text
    doesn't make (the failure the operator saw: Ramp/Anthropic US-only roles reading
    as bare "Remote").
    """
    if workplace != "remote":
        return "unspecified"
    folded = _fold(location or "")
    residual = [t for t in re.split(r"[^a-z0-9]+", folded) if t and t not in _SCOPE_FILLER]
    if residual:
        return "restricted"
    if _contains_any(folded, _SCOPE_GLOBAL):
        return "anywhere"
    return "unspecified"


# --- relocation / visa support ---------------------------------------------
# Whether the posting says the EMPLOYER helps you move for the role — relocation
# assistance or visa sponsorship (``offered``), an explicit refusal (``none``), or
# silence (``unspecified``). Deterministic and offline like the rest (§4.6); unlike
# remote_scope this DOES read the body, because relocation is only ever stated there.
# Negation-aware and conservative: a support cue ("relocation package", "sponsor a
# visa") counts as an offer only when no negator sits right before OR right after it,
# so "no relocation assistance" / "relocation is not offered" never read as an offer;
# a curated set of complete refusal phrases then marks the explicit ``none``. A real
# offer wins over an unrelated refusal elsewhere (a role can offer relocation yet not
# sponsor visas), so the offer test runs first.

_RELOC_NEG: frozenset[str] = frozenset(
    {
        "no", "not", "without", "cannot", "cant", "dont", "doesnt", "wont",
        "unable", "never", "excluding", "nao", "sem", "nenhum", "nenhuma",
    }
)

# Un-negated presence of any of these ⇒ ``offered`` (English + folded pt-BR).
_RELOC_SUPPORT: tuple[str, ...] = (
    "relocation assistance", "relocation support", "relocation package",
    "relocation bonus", "relocation allowance", "relocation stipend",
    "relocation reimbursement", "relocation benefit", "relocation benefits",
    "paid relocation", "cover relocation", "covers relocation",
    "help you relocate", "help with relocation", "assist with relocation",
    "we offer relocation", "offer relocation", "offers relocation",
    "provide relocation", "provides relocation", "relocation provided",
    "relocation offered", "relocation available", "will relocate you",
    "visa sponsorship", "sponsor a visa", "sponsor your visa", "sponsor visas",
    "visa support", "immigration support", "immigration assistance",
    "relocation and visa", "visa and relocation",
    "auxilio realocacao", "auxilio mudanca", "auxilio a mudanca",
    "pacote de realocacao", "apoio a mudanca", "apoio a realocacao",
    "ajuda na mudanca", "realocacao oferecida", "patrocinio de visto",
)

# Complete refusal phrases ⇒ ``none`` (checked only after the offer test fails).
_RELOC_DENY: tuple[str, ...] = (
    "no relocation", "without relocation", "relocation not offered",
    "relocation is not offered", "relocation not available",
    "relocation is not available", "relocation not provided",
    "relocation is not provided", "not offer relocation",
    "does not offer relocation", "do not offer relocation",
    "cannot offer relocation", "unable to offer relocation",
    "no relocation assistance", "no relocation support", "no relocation package",
    "no visa sponsorship", "no visa support", "no sponsorship",
    "without sponsorship", "not sponsor", "cannot sponsor", "unable to sponsor",
    "do not sponsor", "does not sponsor", "not able to sponsor",
    "sem realocacao", "sem auxilio realocacao", "nao oferecemos realocacao",
    "nao oferece realocacao", "nao ha realocacao",
)


def _reloc_signal(text: str) -> str | None:
    """Scan the support cues: ``offered`` at the first un-negated one, else ``none`` if any
    appeared but every occurrence was negated, else ``None`` (no support cue at all).

    The negation window is two-sided because a refusal sits on either side of the cue:
    "no relocation assistance" (before) and "relocation assistance is not offered" (after).
    A single clean offer outranks refusals elsewhere, so it returns immediately.
    """
    signal: str | None = None
    for phrase in _RELOC_SUPPORT:
        start = text.find(phrase)
        while start != -1:
            end = start + len(phrase)
            # Bound the negation window to the cue's own clause: a negator across a
            # sentence/clause break belongs to a different statement ("relocation
            # support; no visa sponsorship" offers relocation — the "no" is the visa's).
            before_clause = re.split(r"[.;!?\n]", text[:start])[-1]
            after_clause = re.split(r"[.;!?\n]", text[end : end + 40], maxsplit=1)[0]
            before = [t for t in re.split(r"[^a-z0-9]+", before_clause) if t][-5:]
            after = [t for t in re.split(r"[^a-z0-9]+", after_clause) if t][:4]
            if not any(t in _RELOC_NEG for t in before) and not any(
                t in _RELOC_NEG for t in after
            ):
                return "offered"
            signal = "none"  # a negated support cue is itself an explicit refusal
            start = text.find(phrase, end)
    return signal


def classify_relocation(title: str, description: str = "") -> str:
    """Employer relocation/visa support stated in the posting: offered / none / unspecified.

    Reads the body — that is where relocation lives. An un-negated relocation or visa
    support cue ⇒ ``offered``; a negated support cue or a complete refusal phrase ⇒
    ``none``; silence ⇒ ``unspecified``. Powers the "offers relocation" browse filter.
    """
    text = _fold((title or "") + ". " + (description or ""))
    signal = _reloc_signal(text)
    if signal == "offered":
        return "offered"
    if signal == "none" or _contains_any(text, _RELOC_DENY):
        return "none"
    return "unspecified"


# --- location normalization ------------------------------------------------
# Freeform location text ("são paulo, brasil", "Remote - US", "NEW YORK, NY") is
# canonicalized to a consistent display + grouping string, WITHOUT losing the
# verbatim value (kept in ``location``, which also feeds ``content_hash``) or ever
# inventing a place the text didn't state. Pure and offline like the rest (§4.6).

# Whole-part synonyms (folded key -> canonical display), applied per comma-part
# before casing so language/abbreviation variants group as one place: "Brasil"/
# "Brazil"; "EUA"/"US"/"USA" -> "United States"; "Remoto" -> "Remote".
_LOCATION_SYNONYMS: dict[str, str] = {
    "brasil": "Brazil",
    "brazil": "Brazil",
    "eua": "United States",
    "us": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "usa": "United States",
    "united states": "United States",
    "united states of america": "United States",
    "estados unidos": "United States",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "united kingdom": "United Kingdom",
    "great britain": "United Kingdom",
    "reino unido": "United Kingdom",
    "uae": "United Arab Emirates",
    "remoto": "Remote",
    "remote": "Remote",
    "trabalho remoto": "Remote",
    "fully remote": "Remote",
    "100% remote": "Remote",
    "anywhere": "Anywhere",
}

# Region/country/US-state codes kept UPPER when a *whole* comma-part equals one
# (bare "ny" -> "NY", "emea" -> "EMEA"). Consulted only for a whole part, so the
# Portuguese connective "de" in "Rio de Janeiro" never trips the Delaware code.
_LOCATION_CODES: frozenset[str] = frozenset(
    {
        "eu", "emea", "apac", "latam", "anz", "mena", "dach", "nyc", "dc",
        "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
        "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
        "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
        "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
        "wi", "wy",
    }
)

# Connectives kept lowercase inside a multi-word part ("Rio de Janeiro", "Isle of
# Man") — never applied to a part's leading token.
_LOCATION_PARTICLES: frozenset[str] = frozenset(
    {"de", "da", "do", "dos", "das", "e", "of", "the", "and", "el", "la", "los", "las"}
)

# Component separators normalized to a comma: pipe/slash/semicolon/middot, or a
# *spaced* en/em/hyphen dash. Intra-word hyphens ("Winston-Salem") are left alone.
_LOCATION_SEP = re.compile(r"\s*(?:[|/;·•]|[-–—]\s|\s[-–—])\s*")


def _title_token(token: str, *, first: bool) -> str:
    """Case one whitespace token of a location part.

    Deliberately-internal capitals ("São", "McLean", "Île-de-France") are trusted
    as-is; an all-lower connective ("de", "of") stays lower unless it leads the
    part; everything else (all-lower or ALL-UPPER) is title-cased.
    """
    if not token:
        return token
    if not first and token.lower() in _LOCATION_PARTICLES:
        return token.lower()
    core = token.replace("'", "").replace("’", "").replace("-", "")
    if core and not (core.islower() or core.isupper()):
        return token  # preserve intentional internal caps
    return token[:1].upper() + token[1:].lower()


def _canon_location_part(part: str) -> str:
    """Canonicalize one comma-separated location component (see below)."""
    cleaned = " ".join(part.split())
    if not cleaned:
        return ""
    folded = _fold(cleaned)
    synonym = _LOCATION_SYNONYMS.get(folded)
    if synonym is not None:
        return synonym
    if folded in _LOCATION_CODES:
        return cleaned.upper()
    tokens = cleaned.split(" ")
    return " ".join(_title_token(t, first=(i == 0)) for i, t in enumerate(tokens))


def normalize_location(raw: str) -> str:
    """Canonicalize freeform location text for display + grouping (§4.6).

    Cleans whitespace, unifies part separators to commas, maps known country /
    region / remote synonyms so cross-language variants group ("Brasil"/"Brazil";
    "EUA"/"US"/"USA"), consistently cases each component (state/region codes upper,
    pt-BR/EN connectives lower, deliberate internal caps preserved), and drops
    duplicate parts. Returns "" when nothing survives. Conservative: it
    standardizes surface form only, never inferring a place the text didn't name.
    """
    if not raw or not raw.strip():
        return ""
    seen: set[str] = set()
    parts: list[str] = []
    for chunk in _LOCATION_SEP.sub(", ", raw).split(","):
        canon = _canon_location_part(chunk)
        if not canon:
            continue
        key = _fold(canon)
        if key in seen:
            continue
        seen.add(key)
        parts.append(canon)
    return ", ".join(parts)


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


# --- title dedup key (§17) ---------------------------------------------------
# Title normalization for the cross-board identity tiers. The measured hazard: the
# vast majority of trailing title segments are CONTENT-bearing ("… - Payments",
# "… (Kubernetes)"), so blanket tail-stripping merges distinct roles. Stripping is
# therefore *whitelist-gated*: a trailing paren/dash/pipe/comma segment is removed
# only when every part of it names a place / remote-ness / timezone. Seniority is
# never stripped (level words distinguish real distinct reqs).

# One decorative/req-number layer: [tags], (m/w/d) diversity boilerplate, "#1234".
_TITLE_TAG = re.compile(r"\[[^\]\[]*\]")
_TITLE_MWD = re.compile(
    r"\(\s*[mwfdhx*]\s*[/|]\s*[mwfdhx*]\s*(?:[/|]\s*(?:[mwfdhx*]|div|divers)\s*)?\)",
    re.IGNORECASE,
)
_TITLE_REQNO = re.compile(r"#\s*\d+\s*$")
_TITLE_LEAD_REMOTE = re.compile(r"^\s*remote\s*[-:–—|]\s*", re.IGNORECASE)
_TITLE_TRAIL_PAREN = re.compile(r"\(([^()]*)\)\s*$")
# Trailing segment after the LAST dash/pipe/comma separator (dashes pre-folded to '-').
_TITLE_TRAIL_SEG = re.compile(r"[-|,]\s*([^\-|,]*)$")
_TZ_PART = re.compile(
    r"^(?:utc|gmt)\s*[+-]?\d{1,2}(?::\d{2})?$"
    r"|^[cepm][sd]t$|^cet$|^cest$|^bst$|^ist$|^aest$|^aedt$|^wet$|^eet$",
    re.IGNORECASE,
)

# Geography/remote-ness vocabulary a strippable tail part must resolve to. Multi-word
# entries are matched after whitespace collapse. Deliberately places + work-mode words
# only — never technologies, teams, or seniority.
_GEO_PARTS: frozenset[str] = frozenset(
    {
        "remote", "hybrid", "onsite", "on-site", "in-office", "anywhere", "worldwide",
        "global", "distributed", "flexible", "americas", "america", "north america",
        "south america", "latin america", "latam", "emea", "apac", "asia", "africa",
        "europe", "oceania", "eu", "eea", "uk", "u.k.", "us", "u.s.", "usa", "us only",
        "united states", "united kingdom", "canada", "germany", "france", "spain",
        "portugal", "netherlands", "poland", "romania", "ukraine", "brazil", "brasil",
        "argentina", "mexico", "colombia", "chile", "peru", "uruguay", "costa rica",
        "india", "singapore", "japan", "korea", "south korea", "china", "australia",
        "new zealand", "ireland", "israel", "austria", "switzerland", "belgium",
        "sweden", "norway", "denmark", "finland", "iceland", "estonia", "latvia",
        "lithuania", "czechia", "czech republic", "slovakia", "hungary", "bulgaria",
        "croatia", "serbia", "greece", "italy", "turkey", "uae", "dubai", "egypt",
        "nigeria", "south africa", "philippines", "vietnam", "indonesia", "thailand",
        "pakistan", "bangladesh", "london", "berlin", "munich", "amsterdam", "paris",
        "madrid", "barcelona", "lisbon", "porto", "dublin", "zurich", "vienna",
        "warsaw", "prague", "bucharest", "sofia", "athens", "stockholm", "oslo",
        "copenhagen", "helsinki", "tallinn", "toronto", "vancouver", "montreal",
        "nyc", "new york", "new york city", "san francisco", "sf", "bay area",
        "boston", "seattle", "austin", "denver", "chicago", "los angeles", "miami",
        "atlanta", "washington dc", "sao paulo", "são paulo", "mexico city",
        "buenos aires", "bogota", "bogotá", "santiago", "lima", "bangalore",
        "bengaluru", "mumbai", "delhi", "hyderabad", "pune", "tokyo", "sydney",
        "melbourne", "tel aviv", "east coast", "west coast", "pacific", "eastern",
        "central", "mountain",
    }
)
# Qualifier words dropped from a part before the vocabulary check ("US only",
# "remote friendly", "EU based", "UK preferred") — they modify a place, not name one.
_GEO_QUALIFIERS: frozenset[str] = frozenset(
    {"only", "based", "preferred", "friendly", "first", "ok", "okay", "eligible"}
)


def _is_geo_segment(segment: str) -> bool:
    """True iff every part of ``segment`` names a place / remote-ness / timezone."""
    seg = segment.strip().lower()
    if not seg:
        return False
    parts = [p.strip() for p in re.split(r"[,/&+]| - | and | or ", seg)]
    parts = [p for p in parts if p]
    if not parts:
        return False
    for part in parts:
        words = [w for w in part.split() if w not in _GEO_QUALIFIERS]
        cleaned = " ".join(words)
        if not cleaned:
            continue  # a bare qualifier riding a sibling part ("US, only")
        if cleaned in _GEO_PARTS or _TZ_PART.match(cleaned):
            continue
        return False
    return True


def normalize_title_key(title: str) -> str:
    """A conservative equality key for a job title (§17), or ``""`` if nothing survives.

    Unescapes entities, casefolds, folds unicode dashes, drops decorative layers
    ([tags], (m/w/d) boilerplate, trailing req numbers, a leading "Remote -"), then
    iteratively strips trailing paren/dash/pipe/comma segments **only** while they are
    pure geography/remote-ness/timezone (see :func:`_is_geo_segment`); any
    content-bearing tail ("… - Payments") stops the loop and survives into the key.
    Used only for corroborating/keying cross-board duplicates — never for display.
    """
    text = html.unescape(title or "").casefold()
    text = re.sub(r"[–—−]", "-", text)
    text = _TITLE_TAG.sub(" ", text)
    text = _TITLE_MWD.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = _TITLE_REQNO.sub("", text).strip()
    text = _TITLE_LEAD_REMOTE.sub("", text)
    while True:
        m = _TITLE_TRAIL_PAREN.search(text)
        if m and _is_geo_segment(m.group(1)):
            text = text[: m.start()].strip()
            continue
        m = _TITLE_TRAIL_SEG.search(text)
        if m and m.start() > 0 and _is_geo_segment(m.group(1)):
            text = text[: m.start()].strip()
            continue
        break
    text = re.sub(r"[^\w\s+#./-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -|,")
    return text.strip()


# --- salary parsing --------------------------------------------------------
# Conservative text→range extraction (§4.6). A currency anchor is *mandatory*: a
# bare '80,000 - 100,000' is ambiguous (which currency?) and returns None — we
# never guess. Numbers come only from ``raw`` (the explicit salary field), never
# mined from the free-text description, so a JD's '$5M in funding' can't leak in.
# Recognize-or-refuse: v2 widened what printed notation is READ (dollar-family
# prefixes, more ISO codes, shared-magnitude shorthand, HTML entities) and added
# refusals where reading would require a guess (day/week rates, ambiguous
# symbols, contradictory periods, funding-scale magnitudes, zeros).

# Bumped on ANY parse_salary behavior change: the store-open repair sweep and the
# salary_raw re-parse are meta-gated on it, so stored rows heal without a re-crawl.
SALARY_PARSER_VERSION = 1

_CUR_NUMBER = re.compile(r"(\d[\d.,]*\d|\d) ?([km])?\b")
_RANGE_SEPS = ("-", "–", "—", "−", " to ", " a ", " ate ", "~")

# Salaries are never printed in billions; a b/bn/billion/trillion magnitude next
# to a number is a funding/revenue figure that leaked into the salary field.
_MAGNITUDE_POISON = re.compile(r"\d\s?(?:bn?\b|billion|trillion)")

# Rates we have no honest annualization factor for — refuse, never invent ×5/×8.
_DAY_WEEK = ("per day", "/day", "por dia", "daily", "per week", "/week", "/wk", "weekly")

# Explicit ISO codes (word-bounded in folded text). Two DIFFERENT codes in one
# string is ambiguous → refuse. A code overrides a bare symbol ('$… CAD' → CAD).
_CURRENCY_CODES: dict[str, str] = {
    "usd": "USD", "eur": "EUR", "euro": "EUR", "euros": "EUR", "gbp": "GBP",
    "brl": "BRL", "reais": "BRL", "cad": "CAD", "aud": "AUD", "sgd": "SGD",
    "nzd": "NZD", "hkd": "HKD", "chf": "CHF", "pln": "PLN", "sek": "SEK",
    "nok": "NOK", "dkk": "DKK", "jpy": "JPY", "inr": "INR", "mxn": "MXN",
    "cny": "CNY", "rmb": "CNY",
}

# Symbol families, most-specific first (bare '$' LAST — 'CA$'/'A$'/'S$' are the
# printed currency, not USD). Ambiguous symbols (¥ = JPY/CNY, kr = SEK/NOK/DKK)
# are deliberately NOT anchors: they refuse instead of guessing.
_CURRENCY_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("r$", "BRL"), ("ca$", "CAD"), ("c$", "CAD"), ("au$", "AUD"), ("a$", "AUD"),
    ("sg$", "SGD"), ("s$", "SGD"), ("nz$", "NZD"), ("hk$", "HKD"), ("mx$", "MXN"),
    ("€", "EUR"), ("£", "GBP"), ("₹", "INR"), ("$", "USD"),
)
_AMBIGUOUS_ANCHORS = ("¥", " kr ", " kr.")

# Currencies whose salary magnitudes make the contradiction bounds meaningful:
# no major prints 700/hour or 150,000/month — such a period is a mis-read.
_CONTRADICTION_MAJORS = frozenset(
    {"USD", "EUR", "GBP", "CAD", "AUD", "SGD", "NZD", "CHF"}
)

# Period markers. Explicit slash / 'per X' / 'por X' forms are safe to read out
# of a description; the bare words (monthly/annual/hourly) are only trusted in
# the raw salary field itself.
_HOUR = ("per hour", "/hour", "/hr", "hourly", "por hora", "/hora", "1 hour")
_MONTH = (
    "per month",
    "/month",
    "/mo",
    "monthly",
    "por mes",
    "/mes",
    "mensal",
    "a month",
    "1 month",
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
    "1 year",
)
# Description-side slash markers count only when NOT glued to a word on the left
# ('$45/hr', 'paid /month' — never 'ERP/HRIS', 'People/HR', '…modal.com/…',
# 'data/model/…') and not running into one on the right.
_DESC_SLASH = {
    "hour": r"(?:^|[^a-z])/\s?(?:hr|hour|hora)(?![a-z])",
    "month": r"(?:^|[^a-z])/\s?(?:mo|month|mes)(?![a-z])",
    "year": r"(?:^|[^a-z])/\s?(?:yr|year|ano)(?![a-z])",
}
_DESC_PHRASES = {
    "hour": ("per hour", "por hora"),
    "month": ("per month", "por mes"),
    "year": ("per year", "per annum", "por ano"),
}


def _detect_currency(text: str) -> str | None:
    """The printed currency anchor, if exactly one exists.

    Explicit ISO codes win over symbols (a '$…-… CAD' band is CAD); two
    DIFFERENT codes refuse (ambiguous). Symbol families are tested
    most-specific-first so 'CA$'/'A$'/'S$' never fall through to the bare-'$'
    USD read, and deliberately-ambiguous symbols (¥, kr) refuse rather than
    guess a country.
    """
    codes = {iso for word, iso in _CURRENCY_CODES.items() if _has(text, (word,))}
    if len(codes) > 1:
        return None
    if len(codes) == 1:
        return next(iter(codes))
    for symbol, iso in _CURRENCY_SYMBOLS:
        if symbol in text:
            return iso
    return None


def _has_ambiguous_anchor(text: str) -> bool:
    """True when the only money marker is one we refuse to guess a country for."""
    padded = f" {text} "
    return any(marker in padded for marker in _AMBIGUOUS_ANCHORS)


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


def _extract_numbers(text: str) -> list[tuple[float, str | None]]:
    """Every number with its printed magnitude suffix ('k'/'m'), in order."""
    out: list[tuple[float, str | None]] = []
    for match in _CUR_NUMBER.finditer(text):
        value = _strip_separators(match.group(1))
        if value is None:
            continue
        suffix = match.group(2)
        if suffix == "k":
            value *= 1000
        elif suffix == "m":
            value *= 1_000_000
        out.append((value, suffix))
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


def _detect_period_in_description(description: str) -> str | None:
    """Boundary-guarded period read from free text (see _DESC_SLASH): a rate
    marker next to a figure, never prose like 'ERP/HRIS' or a URL path."""
    for period in ("hour", "month", "year"):
        if re.search(_DESC_SLASH[period], description) or _contains_any(
            description, _DESC_PHRASES[period]
        ):
            return period
    return None


def _default_period(currency: str) -> str:
    """BRL postings quote monthly by convention; USD/EUR/GBP quote yearly."""
    return "month" if currency == "BRL" else "year"


def parse_salary(raw: str, description: str = "") -> SalaryRange | None:
    """Extract a :class:`SalaryRange` from verbatim salary text, or ``None``.

    Conservative (§4.6), recognize-or-refuse: returns ``None`` unless *both* an
    unambiguous currency anchor and at least one positive number are present in
    ``raw``. A range needs two numbers separated by a dash / 'to' / 'até'; a
    shared-magnitude shorthand range ('$80-100k', 'EUR 80-110,000') reads the
    second number's printed magnitude onto the first; a lone number yields a
    point band. Period comes from an explicit marker in ``raw`` (including the
    Ashby '/ 1 HOUR' vocabulary), else a boundary-guarded slash/'per' marker in
    ``description``, else the currency's convention. Refusals — never guesses:
    day/week rates (no honest annualization factor), b/trillion magnitudes
    (funding figures), ambiguous symbols (¥, kr), two different ISO codes,
    zeros, and periods the printed figures contradict (no major currency pays
    700/hour or 150,000/month).
    """
    text = _fold(html.unescape(raw or ""))
    if _MAGNITUDE_POISON.search(text):
        return None
    if _contains_any(text, _DAY_WEEK):
        return None
    currency = _detect_currency(text)
    if currency is None:
        return None
    numbers = _extract_numbers(text)
    if not numbers:
        return None
    if len(numbers) >= 2 and _contains_any(text, _RANGE_SEPS):
        (lo, lo_suffix), (hi, hi_suffix) = sorted(numbers[:2], key=lambda p: p[0])
        # Shared-magnitude shorthand: a bare small first bound next to a
        # k/m-suffixed (or thousands-printed) second is the SAME magnitude as
        # printed — '$80-100k' means 80k. Guarded so '$500-45,000' (a genuine
        # low bound) is left alone.
        if lo_suffix is None and lo < 1000:
            if hi_suffix == "m" and lo * 1_000_000 <= hi * 2:
                lo *= 1_000_000
            elif (hi_suffix == "k" or hi >= 1000) and lo * 1000 <= hi * 2:
                lo *= 1000
    else:
        lo = hi = numbers[0][0]
    if lo <= 0 or hi <= 0:
        return None
    period = (
        _detect_period(text, _HOUR, _MONTH, _YEAR)
        or _detect_period_in_description(_fold(description or ""))
        or _default_period(currency)
    )
    if currency in _CONTRADICTION_MAJORS:
        if period == "hour" and hi > 500:
            return None
        if period == "month" and hi > 100_000:
            return None
        if hi * _PERIODS_PER_YEAR.get(period, 1.0) > 5_000_000:
            return None
    return SalaryRange(lo, hi, currency, period)


# --- compensation normalization (annualized USD) ---------------------------
# A salary's comparability axis is annualized US dollars. Source ranges arrive in
# their own currency (USD/EUR/GBP/BRL) and period (year/month/hour); to sort and
# filter one board's BRL-monthly coherently against another's USD-annual, both are
# folded onto a single annualized-USD figure. The *display* keeps the source range
# — this is only the ranking/filtering key (§4.6).

# Work-hours per year for hourly→annual (40h × 52wk); monthly is ×12.
_HOURS_PER_YEAR = 2080.0
_PERIODS_PER_YEAR: dict[str, float] = {
    "year": 1.0,
    "month": 12.0,
    "hour": _HOURS_PER_YEAR,
}

# Static fallback spot rates: 1 unit of currency in USD — the floor that keeps the
# free-text majors convertible before the first live pull, and the default when no live
# table is supplied. Enrichment stays a pure, offline function (§4.6/§4.9) with no network
# in the crawl path, so live rates are fetched out-of-band (:mod:`jobcrawler.fx`, daily)
# into the DB and overlaid via :meth:`StateStore.fx_table`, handed in as ``fx_rates``.
# Coarse ranking tolerates FX drift (bands are wide, and we sort/filter, not invoice).
# USD is the identity. Keys match the currencies ``_detect_currency`` emits.
FX_TO_USD: dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "BRL": 0.18,
}


def usd_annual_band(
    salary_min: float | None,
    salary_max: float | None,
    currency: str | None,
    period: str | None,
    *,
    fx_rates: dict[str, float] | None = None,
) -> tuple[float | None, float | None]:
    """Fold a source ``(min, max, currency, period)`` salary onto an annualized-USD band.

    The coherent cross-currency key behind the salary sort + ``min_salary`` filter
    (§4.6): each bound is scaled by the currency's USD rate and the period's yearly
    factor. Returns ``(None, None)`` when the currency or period can't be resolved —
    never a half-converted figure — so an unrecognized salary reads as *unstated*
    rather than silently mis-ranked. A ``None`` bound stays ``None`` (open-ended band).

    ``fx_rates`` is the currency→USD table to convert against; it defaults to the static
    :data:`FX_TO_USD`. The host passes :meth:`StateStore.fx_table` (static floor overlaid
    with refreshed live rates) so current rates are used without this pure step ever
    touching the network.
    """
    table = FX_TO_USD if fx_rates is None else fx_rates
    rate = table.get((currency or "").strip().upper())
    factor = _PERIODS_PER_YEAR.get((period or "").strip().lower())
    if rate is None or factor is None:
        return (None, None)
    scale = rate * factor
    lo = None if salary_min is None else salary_min * scale
    hi = None if salary_max is None else salary_max * scale
    return (lo, hi)


# --- top-level enrichment --------------------------------------------------


def _salary_from_fields(job: Job) -> SalaryRange | None:
    """Build a range from the crawler's *structured* salary fields, if it set
    them (the source exposed structured compensation). ``None`` → fall back to
    parsing the verbatim ``salary`` text. Zero/negative bounds refuse: APIs send
    0 for "unstated" (devjobsscanner salaryMin=0) and zero is absence, never a
    floor."""
    if job.salary_min is None and job.salary_max is None:
        return None
    lo = job.salary_min if job.salary_min is not None else job.salary_max
    hi = job.salary_max if job.salary_max is not None else job.salary_min
    if lo is None or hi is None:
        return None
    if lo <= 0 or hi <= 0:
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
    salary_origin: str | None = "structured" if salary is not None else None
    if salary is None:
        salary = parse_salary(job.salary or "", job.description or "")
        if salary is not None:
            salary_origin = "text"
    workplace = classify_workplace(
        job.workplace_type or "", job.location, job.description
    )
    return EnrichedJob(
        job=job,
        discipline=classify_discipline(job.title, job.description),
        level=classify_level(job.title, job.description),
        workplace=workplace,
        remote_scope=classify_remote_scope(workplace, job.location),
        relocation=classify_relocation(job.title, job.description or ""),
        salary=salary,
        salary_origin=salary_origin,
    )
