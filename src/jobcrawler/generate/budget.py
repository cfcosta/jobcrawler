"""Host-side generation cost backstops (§4.2).

``dspy.RLM``'s own caps bound a *cooperative* model: ``max_iterations`` hard-bounds
the root loop, but ``max_llm_calls`` raises a **recoverable** in-sandbox error the
generated code can catch and loop past — so it does not hard-bound sub-calls, and
nothing bounds the fan-out that multiplies whole generations. The host therefore
adds the **only unconditional** stop: a sub-call ceiling (``JOBCRAWLER_MAX_SUBCALLS``,
set ≥ ``max_llm_calls``) enforced *outside* the sandbox, on the ``sub_lm`` the RLM
calls for every ``llm_query``. The kill is a :class:`SubcallBudgetExceeded`, a
``BaseException`` the untrusted crawler's ``except Exception`` cannot swallow, so it
truly aborts the trajectory regardless of a catch-and-continue loop.
"""

from __future__ import annotations

from typing import Any

import dspy


class SubcallBudgetExceeded(BaseException):
    """The unconditional host sub-call kill firing (§4.2).

    Subclasses ``BaseException`` (not ``Exception``) on purpose: a generated crawler
    that wraps ``llm_query`` in ``except Exception`` must not be able to catch this
    and keep looping — it is the one hard stop on a catch-and-continue sub-call loop.
    """


class SubcallBudget:
    """A per-trajectory sub-call counter (§4.2).

    ``charge()`` once per ``llm_query``; it raises :class:`SubcallBudgetExceeded` the
    moment the count passes ``limit``. ``reset()`` at the start of each generation —
    one RLM run is one trajectory, and a single ``sub_lm`` is reused across the
    fan-out and the whole site batch, so the budget must be reset per trajectory or
    later candidates/sites would inherit an exhausted count.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self.used = 0

    def charge(self) -> None:
        self.used += 1
        if self.used > self._limit:
            raise SubcallBudgetExceeded(
                f"sub-call budget exhausted: {self.used} > {self._limit} "
                "(JOBCRAWLER_MAX_SUBCALLS) — aborting the generation trajectory"
            )

    def reset(self) -> None:
        self.used = 0


class CappedLM(dspy.LM):
    """A ``sub_lm`` that enforces the §4.2 sub-call ceiling.

    Charges the budget on every call (``__call__`` is the path ``dspy.RLM`` uses for
    ``llm_query``; ``acall`` is covered too — in dspy 3.x each delegates to
    ``forward``/``aforward``, so this counts once per logical call) and aborts via
    :class:`SubcallBudgetExceeded` past the limit. Otherwise a transparent
    ``dspy.LM``: ``dspy.LM.copy()`` deep-copies rather than re-constructs, so the
    counter rides through the ``rollout_id`` copies RLM may make. Build one per
    generation run and :meth:`reset_subcalls` per trajectory (the generator does).
    """

    def __init__(self, model: str, *, max_subcalls: int, **kwargs: Any) -> None:
        super().__init__(model, **kwargs)
        self._budget = SubcallBudget(max_subcalls)

    def reset_subcalls(self) -> None:
        self._budget.reset()

    def __call__(self, *args: Any, **kwargs: Any) -> list[dict[str, Any] | str]:
        self._budget.charge()
        return super().__call__(*args, **kwargs)

    async def acall(self, *args: Any, **kwargs: Any) -> list[dict[str, Any] | str]:
        self._budget.charge()
        return await super().acall(*args, **kwargs)


def reset_subcalls(lm: object) -> None:
    """Reset a sub_lm's per-trajectory budget if it tracks one (no-op otherwise).

    Duck-typed so the generator stays agnostic to whether its ``sub_lm`` is a
    :class:`CappedLM` (production) or a plain/fake LM (tests, optimizer replicas).
    """
    reset = getattr(lm, "reset_subcalls", None)
    if callable(reset):
        reset()
