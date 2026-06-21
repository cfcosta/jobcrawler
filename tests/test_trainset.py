"""Tests for the §4.11 trainset (accepted snapshot→crawler examples)."""

from __future__ import annotations

from bizup_autocrawler.trainset import (
    INPUT_FIELDS,
    admit,
    assert_disjoint,
    load_trainset,
    make_example,
    save_trainset,
)


def _example(site_url: str = "https://a.com"):
    return make_example(
        site_url=site_url,
        corpus={"homepage": "h", "articles": []},
        render="auto",
        hints="",
        discovery_method="feed",
        discovery_url=f"{site_url}/feed",
        crawler_source="SITE='x'\ndef crawl(ctx):\n    yield None\n",
    )


def test_example_is_inputs_only() -> None:
    ex = _example()
    assert set(ex.inputs().keys()) == set(INPUT_FIELDS)
    # the recorded outputs are provenance, not labels (the metric re-runs a crawler).
    assert ex.discovery_method == "feed"


def test_admit_requires_gates_adversarial_and_stability() -> None:
    assert admit(
        gates_passed=True, adversarial_passed=True, stable_runs=3, required_stable=3
    )
    assert not admit(
        gates_passed=False, adversarial_passed=True, stable_runs=5, required_stable=3
    )
    assert not admit(
        gates_passed=True, adversarial_passed=False, stable_runs=5, required_stable=3
    )
    assert not admit(
        gates_passed=True, adversarial_passed=True, stable_runs=2, required_stable=3
    )


def test_assert_disjoint_passes_for_separate_sites() -> None:
    train = [_example("https://a.com"), _example("https://b.com")]
    val = [_example("https://c.com")]
    promo = [_example("https://d.com")]
    assert_disjoint(train, val, promo)  # no raise


def test_assert_disjoint_rejects_a_leak() -> None:
    train = [_example("https://a.com")]
    promo = [_example("https://a.com")]  # same site in train + promotion = leak
    try:
        assert_disjoint(train, promo)
        raise AssertionError("expected a leak to be rejected")
    except ValueError as exc:
        assert "a.com" in str(exc)


def test_save_load_round_trip(tmp_path: object) -> None:
    examples = [_example("https://a.com"), _example("https://b.com")]
    path = f"{tmp_path}/trainset.json"  # type: ignore[str-bytes-safe]
    save_trainset(examples, path)
    loaded = load_trainset(path)
    assert [ex.site_url for ex in loaded] == ["https://a.com", "https://b.com"]
    assert set(loaded[0].inputs().keys()) == set(INPUT_FIELDS)
    assert loaded[0].corpus == {"homepage": "h", "articles": []}
