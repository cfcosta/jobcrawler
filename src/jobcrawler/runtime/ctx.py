"""The shared fetch boundary handed to every crawler (§4.5).

All fetching goes through ``ctx`` so SSRF (§8), redirects, encoding, timeouts
and (later) robots/rate-limit/Cloudflare are enforced centrally — the crawler
declares *what* to fetch, not *how*. ``fetch`` re-checks the SSRF guard on every
redirect hop (a redirect to an internal target would otherwise bypass it), and
is method-general (§8): a JSON board API that only answers POST rides exactly
the same guard/pacing/budget path as a GET — the crawler chooses the verb,
never the enforcement. ``render`` (the headless Playwright tier) lands in M2.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Collection, Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import httpx

from jobcrawler import config
from jobcrawler.durations import parse_retry_after
from jobcrawler.runtime.encoding import (
    http_charset_from_content_type,
    resolve_text,
)
from jobcrawler.runtime.politeness import (
    Pacer,
    Robots,
    RobotsDisallowedError,
)
from jobcrawler.runtime.solver import (
    ChallengeError,
    ChallengeSolver,
    Clearance,
    ClearanceCache,
    is_challenge,
)
from jobcrawler.runtime.ssrf import SsrfError, check_url, is_on_domain
from jobcrawler.urls import normalize_host

_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
# Transient HTTP statuses worth a backed-off retry (§4.5): rate-limit + the gateway
# overload family. 500 is excluded — it's usually a deterministic app error, not a
# blip. A 403/503 that is a Cloudflare *challenge* is filtered out separately, since
# that escalates to the solver rather than a blind retry.
_RETRYABLE_CODES = frozenset({429, 502, 503, 504})


class PageLimitError(RuntimeError):
    """The crawl exceeded its per-run page budget (``JOBCRAWLER_MAX_PAGES``)."""


def _is_transient(resp: httpx.Response) -> bool:
    """A retryable response (§4.5): a transient status that is not a CF challenge."""
    return resp.status_code in _RETRYABLE_CODES and not is_challenge(resp)


class Ctx:
    def __init__(
        self,
        allowed_hosts: Collection[str],
        *,
        client: httpx.Client | None = None,
        proxy: str | None = None,
        user_agent: str | None = None,
        timeout: float = 20.0,
        solver: ChallengeSolver | None = None,
        clearance: ClearanceCache | None = None,
        max_pages: int | None = None,
        pacer: Pacer | None = None,
        robots: Robots | None = None,
        retries: int | None = None,
        backoff: float | None = None,
        retry_cap: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._allowed = set(allowed_hosts)
        self._pages = 0
        self._max_pages = config.max_pages() if max_pages is None else max_pages
        self._solver = solver
        self._clearance = clearance
        self._pacer = pacer  # §4.5 per-host throttle; None disables (bare-Ctx tests)
        self._robots = robots  # §4.5 robots.txt gate; None disables
        # §4.5 transient-retry policy; ``sleep`` is injectable so tests assert the
        # backoff schedule without actually waiting.
        self._retries = config.fetch_retries() if retries is None else retries
        self._backoff = config.fetch_backoff() if backoff is None else backoff
        self._retry_cap = config.fetch_retry_cap() if retry_cap is None else retry_cap
        self._sleep = sleep
        # ``proxy`` (the #19b egress chokepoint) is an explicit belt over the trust_env
        # proxy env vars, so the bare live Ctx routes through the loopback allowlist proxy
        # even if trust_env is ever disabled. Ignored when a client is injected (the
        # offline replay/MockTransport path, which must not need a live proxy).
        self._client = client or httpx.Client(
            headers={"user-agent": user_agent or config.user_agent()},
            follow_redirects=False,
            timeout=timeout,
            proxy=proxy,
        )

    @property
    def pages_fetched(self) -> int:
        """Logical pages fetched this run (one per fetch/get), for the run record."""
        return self._pages

    def _charge_page(self) -> None:
        # The committed-crawl resource bound that actually interrupts a runaway
        # crawler in-process (§4.4): the next fetch past the cap raises, and the
        # runner isolates it. Full subprocess isolation of reviewed crawls is M2.
        if self._max_pages is not None and self._pages >= self._max_pages:
            raise PageLimitError(f"page budget exhausted ({self._max_pages} pages)")
        self._pages += 1

    def fetch(
        self,
        url: str,
        *,
        max_redirects: int = 5,
        method: str = "GET",
        json: object | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> str:
        """Request ``url`` (SSRF-checked per hop) and return decoded UTF-8 text.

        ``method``/``json``/``headers`` cover the JSON-API boards whose
        enumeration endpoint only answers POST (a search API): the request rides
        the same SSRF guard, politeness gate, page budget, and transient-retry
        policy as a GET — the crawler chooses the verb, never the enforcement
        (§8). Redirects follow browser convention: 301/302/303 downgrade a
        non-GET to a body-less GET, 307/308 replay the method and body — and
        every hop is re-checked either way.

        On a detected Cloudflare challenge ``fetch`` escalates transparently to
        the solver tier and replays the cached clearance (§4.10) — crawler code
        stays oblivious.
        """
        self._charge_page()
        current, verb, body = url, method.upper(), json
        for _ in range(max_redirects + 1):
            check_url(current, self._allowed)
            self._pace(current)
            resp = self._challenge_aware_request(
                verb, current, json=body, headers=headers
            )
            if resp.status_code in _REDIRECT_CODES and "location" in resp.headers:
                current = urljoin(current, resp.headers["location"])
                if resp.status_code not in (307, 308) and verb != "GET":
                    verb, body = "GET", None  # browser-convention method downgrade
                continue
            resp.raise_for_status()
            return self._decode(resp)
        raise SsrfError(f"too many redirects from {url!r}")

    def resolve(
        self,
        url: str,
        *,
        max_redirects: int = 5,
        headers: Mapping[str, str] | None = None,
    ) -> str:
        """Follow ``url``'s redirect chain and return where it lands — as data.

        The tracking-hop primitive (§8): aggregator boards route their apply
        CTAs through on-site trackers (``/i/<token>`` → ``/z/<salted-hash>`` →
        302 to the real ATS posting) whose *value is the final Location, not
        any body*. ``fetch`` cannot serve that — it follows hops internally,
        would demand the foreign ATS host be on the crawler's allowlist, and
        would download a page nobody wants.

        Every hop actually *requested* rides the full fetch discipline — the
        per-hop SSRF check, politeness pacing, challenge escalation, and one
        page charged for the whole chain, exactly like ``fetch``. The moment a
        hop points OFF the crawler's allowlist, that target is returned
        verbatim WITHOUT being requested: it is captured as data (an
        ``apply_url``), so host admission stays exactly as narrow as
        ``fetch``'s. The off-allowlist target must still clear the
        internal-address screen — a tracker pointing at loopback or a metadata
        endpoint is an attack, not an apply link, and must not launder into
        stored data the UI or autofill daemon would later open. A chain that
        ends on-site with a non-redirect returns the last URL after raising
        for an error status; exhausting ``max_redirects`` raises like
        ``fetch``.
        """
        self._charge_page()
        current = url
        for _ in range(max_redirects + 1):
            check_url(current, self._allowed)
            self._pace(current)
            resp = self._challenge_aware_request("GET", current, headers=headers)
            if resp.status_code in _REDIRECT_CODES and "location" in resp.headers:
                target = urljoin(current, resp.headers["location"])
                if is_on_domain(normalize_host(target), self._allowed):
                    current = target  # on-allowlist hop: keep following
                    continue
                # Off-allowlist: this is the answer. Screen the name exactly as
                # a fetch would — the single-member allowlist makes the
                # on-domain rule vacuous so only the blocked-internal checks
                # bite — then hand it back unrequested.
                check_url(target, {normalize_host(target)})
                return target
            resp.raise_for_status()
            return current
        raise SsrfError(f"too many redirects from {url!r}")

    @staticmethod
    def _decode(resp: httpx.Response) -> str:
        # The §4.5 ladder, not httpx's bare ``.text`` (which only honors the HTTP
        # charset and otherwise assumes UTF-8): fall through to BOM, <meta>, then
        # statistical detection so legacy pages don't come back as mojibake.
        #
        # ``resp.content`` materializes the WHOLE body on purpose — no byte cap here.
        # A committed crawl's primary targets are ATS JSON boards that routinely run
        # 0.7–3MB (Ashby/Greenhouse whole-board responses; large boards reach ~12MB),
        # and any truncation would slice a JSON document mid-object and make it
        # unparseable, silently starving the crawl of postings. The backstop against a
        # decompression/response bomb is the §4.4 sandbox memory rlimit (RLIMIT_AS),
        # which kills a runaway child cleanly — a size cap in this decode path is the
        # wrong layer and would corrupt legitimate large boards, so we deliberately
        # keep none.
        return resolve_text(
            resp.content,
            http_charset_from_content_type(resp.headers.get("content-type")),
        )

    def _challenge_aware_request(
        self,
        method: str,
        url: str,
        *,
        json: object | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        host = normalize_host(url)
        cached = self._clearance.get(host) if self._clearance is not None else None
        # Caller headers survive under the clearance's (an API's mandatory Accept
        # rides alongside the solved cookie); the clearance wins a collision —
        # replaying it verbatim is the whole point of the cache (§4.10).
        resp = self._request_retrying(
            method, url, json=json, headers=self._merge(headers, cached)
        )
        if self._solver is None or not is_challenge(resp):
            return resp
        # "Pay once": solve, cache, replay. Still challenged → give up (§4.10).
        clearance = self._solver.solve(url)
        if self._clearance is not None:
            self._clearance.put(host, clearance)
        resp = self._request_retrying(
            method, url, json=json, headers=self._merge(headers, clearance)
        )
        if is_challenge(resp):
            raise ChallengeError(f"still challenged after solve: {url!r}")
        return resp

    @staticmethod
    def _merge(
        headers: Mapping[str, str] | None, clearance: Clearance | None
    ) -> dict[str, str]:
        merged = dict(headers or {})
        if clearance is not None:
            merged.update(clearance.headers())
        return merged

    def _request_retrying(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """``client.request`` with bounded exponential backoff on transient failure (§4.5).

        Retries a transient response (:data:`_RETRYABLE_CODES`, never a Cloudflare
        challenge) or a transient transport error, preferring a server ``Retry-After``
        over the computed backoff. A ``Retry-After`` longer than ``retry_cap`` is
        refused — we surface the response rather than park the crawl — and a network
        error keeps retrying until the attempt budget is spent, then re-raises.
        Method-blind on purpose: the only POSTs on this path are board search APIs —
        idempotent reads in POST clothing — so the GET retry policy carries over.
        """
        attempt = 0
        while True:
            try:
                resp = self._client.request(method, url, **kwargs)
            except httpx.TransportError:
                if attempt >= self._retries:
                    raise
                self._sleep(self._expo(attempt))
                attempt += 1
                continue
            if attempt >= self._retries or not _is_transient(resp):
                return resp
            delay = self._retry_delay(resp, attempt)
            if delay is None:  # Retry-After past the cap: give up, don't block
                return resp
            self._sleep(delay)
            attempt += 1

    def _retry_delay(self, resp: httpx.Response, attempt: int) -> float | None:
        """Seconds to wait before the next retry, or ``None`` to stop retrying.

        A parseable ``Retry-After`` wins (``None`` if it exceeds ``retry_cap``);
        otherwise the capped exponential backoff for this attempt.
        """
        header = resp.headers.get("retry-after")
        if header is not None:
            after = parse_retry_after(header, datetime.now(timezone.utc))
            if after is not None:
                return None if after > self._retry_cap else after
        return self._expo(attempt)

    def _expo(self, attempt: int) -> float:
        return min(self._backoff * (2**attempt), self._retry_cap)

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        check_url(url, self._allowed)
        self._charge_page()
        self._pace(url)
        # Never auto-follow: a redirect target is unchecked, so following one
        # (e.g. 302 → 169.254.169.254) would bypass the SSRF guard. The caller
        # re-fetches the Location through ctx, which re-checks it.
        kwargs["follow_redirects"] = False
        return self._request_retrying("GET", url, **kwargs)

    def _pace(self, url: str) -> None:
        """Politeness gate before a network fetch (§4.5).

        robots.txt is consulted first — a disallowed path raises before any request
        and before pacing (no point sleeping to refuse) — then the host is throttled,
        with its floor raised to any robots ``Crawl-delay``. Both layers are optional:
        without a robots policy or pacer this is a no-op (bare-Ctx tests, M0/M1).
        """
        delay: float | None = None
        if self._robots is not None:
            if not self._robots.allowed(url):
                raise RobotsDisallowedError(url)
            delay = self._robots.crawl_delay(url)
        if self._pacer is not None:
            self._pacer.wait(normalize_host(url), min_interval=delay)

    def render(self, url: str) -> str:
        raise NotImplementedError(
            "headless rendering is the M2 Playwright tier (§4.10)"
        )

    def log(self, *args: object, **kwargs: object) -> None:
        # stderr, never stdout: the child's stdout is discarded (§8, the result
        # travels on a private file), and stderr is what the parent captures for a
        # crash tail — so a crawler's own log lines survive to explain a failure.
        # kwargs are ignored (not forwarded to print) so a crawler can't redirect
        # the stream with file=...
        print("[crawl]", *args, file=sys.stderr)


if TYPE_CHECKING:
    from jobcrawler.runtime.contract import Ctx as _CtxProtocol


def crawler_facade(ctx: Ctx) -> _CtxProtocol:
    """Wrap the enforcing :class:`Ctx` in the capability object a crawler receives (§8).

    :class:`Ctx` keeps its enforcement state — the SSRF allowlist (``_allowed``),
    the page budget (``_max_pages``), the pacer, the live ``httpx`` client — in
    *single-underscore* attributes, and the AST gate deliberately permits
    single-underscore attribute access. That combination let hostile crawler code
    reach ``ctx._client`` (a raw client → requests straight past the SSRF guard),
    null ``ctx._max_pages`` / ``ctx._pacer`` (kill the budget and politeness), or
    call ``ctx._allowed.add(...)`` (widen the allowlist) — the enforcing object was
    handed to the very code it constrains.

    So the crawler never touches the real ctx. It gets this facade: a
    ``__slots__ = ()`` object whose only members are the three capability methods,
    each *closing over* the real ctx as a free variable. The ctx lives in the
    methods' ``__closure__`` — a dunder the gate rejects — and prising it out would
    also need ``getattr``, which is banned. There is no instance attribute (single
    underscore or otherwise) to pivot through: enforcement is unreachable from
    crawler code, not merely awkward to reach. ``get`` (a raw ``httpx.Response``
    leak) is intentionally absent — it is off the crawler contract entirely.
    """

    class _Facade:
        __slots__ = ()

        def fetch(
            self,
            url: str,
            *,
            max_redirects: int = 5,
            method: str = "GET",
            json: object | None = None,
            headers: Mapping[str, str] | None = None,
        ) -> str:
            return ctx.fetch(
                url,
                max_redirects=max_redirects,
                method=method,
                json=json,
                headers=headers,
            )

        def resolve(
            self,
            url: str,
            *,
            max_redirects: int = 5,
            headers: Mapping[str, str] | None = None,
        ) -> str:
            return ctx.resolve(url, max_redirects=max_redirects, headers=headers)

        def render(self, url: str) -> str:
            return ctx.render(url)

        def log(self, *args: object, **kwargs: object) -> None:
            ctx.log(*args, **kwargs)

    return _Facade()
