"""Tests for the §4.11 trainset (accepted snapshot→crawler examples)."""

from __future__ import annotations

from jobcrawler.trainset import (
    INPUT_FIELDS,
    admit,
    admitted_examples,
    append_record,
    assert_disjoint,
    load_records,
    load_trainset,
    make_example,
    optimization_pool,
    record_failure,
    save_trainset,
    split_by_site,
)


def _example(site_url: str = "https://a.com"):
    return make_example(
        site_url=site_url,
        corpus={"homepage": "h", "postings": []},
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
    assert loaded[0].corpus == {"homepage": "h", "postings": []}


def test_append_and_load_records(tmp_path: object) -> None:
    path = f"{tmp_path}/records.jsonl"  # type: ignore[str-bytes-safe]
    append_record(path, slug="stripe", example=_example("https://stripe.com"))
    append_record(path, slug="gitlab", example=_example("https://gitlab.com"))

    records = load_records(path)

    assert [r["slug"] for r in records] == ["stripe", "gitlab"]
    assert records[0]["corpus"] == {"homepage": "h", "postings": []}


def test_load_records_absent_is_empty(tmp_path: object) -> None:
    assert load_records(f"{tmp_path}/nope.jsonl") == []  # type: ignore[str-bytes-safe]


def test_admitted_examples_filters_by_stable_runs(tmp_path: object) -> None:
    # Records are written on §6+§8 pass; admission here is the per-site stability
    # condition — a site below the required stable-run count is held back (§4.11).
    path = f"{tmp_path}/records.jsonl"  # type: ignore[str-bytes-safe]
    append_record(path, slug="ready", example=_example("https://ready.com"))
    append_record(path, slug="fresh", example=_example("https://fresh.com"))
    stable = {"ready": 3, "fresh": 1}

    admitted = admitted_examples(
        load_records(path), stable_runs=lambda s: stable.get(s, 0), required_stable=3
    )

    assert [ex.site_url for ex in admitted] == ["https://ready.com"]
    assert set(admitted[0].inputs().keys()) == set(INPUT_FIELDS)


def test_split_by_site_is_disjoint() -> None:
    examples = [_example(f"https://s{i}.com") for i in range(4)]

    train, val, holdout = split_by_site(examples, holdout=1, val=1)

    assert_disjoint(train, val, holdout)  # no leak across pools
    assert (len(train), len(val), len(holdout)) == (2, 1, 1)


def test_optimization_pool_includes_failures_as_signal(tmp_path: object) -> None:
    # A stable success AND a confirmed failure both enter the pool; the failure is the
    # sub-maximal signal the optimizer needs, scored on a freshly re-prefetched corpus.
    path = f"{tmp_path}/records.jsonl"  # type: ignore[str-bytes-safe]
    append_record(path, slug="good", example=_example("https://good.com"))
    record_failure(path, slug="drifted", site_url="https://drifted.com", hints="h")

    pool = optimization_pool(
        load_records(path),
        stable_runs=lambda s: 3 if s == "good" else 0,
        required_stable=3,
        prefetch=lambda url: {"homepage": f"fresh:{url}"},
    )

    by_site = {ex.site_url: ex for ex in pool}
    assert set(by_site) == {"https://good.com", "https://drifted.com"}
    assert by_site["https://drifted.com"].corpus == {
        "homepage": "fresh:https://drifted.com"
    }


def test_optimization_pool_holds_unstable_accepts_but_keeps_failures(
    tmp_path: object,
) -> None:
    path = f"{tmp_path}/records.jsonl"  # type: ignore[str-bytes-safe]
    append_record(path, slug="fresh", example=_example("https://fresh.com"))
    record_failure(path, slug="broken", site_url="https://broken.com")

    pool = optimization_pool(
        load_records(path),
        stable_runs=lambda s: 1,  # below the bar → the accept is held back
        required_stable=3,
        prefetch=lambda url: {"homepage": "x"},
    )

    # The unstable accept is held back, but the failure stays — failures are the point.
    assert [ex.site_url for ex in pool] == ["https://broken.com"]


def test_split_by_site_keeps_a_site_whole() -> None:
    # Two examples for the same site must land in the SAME pool (a site split across
    # pools is the train/val leak assert_disjoint rejects).
    examples = [
        _example("https://a.com"),
        _example("https://a.com"),
        _example("https://b.com"),
    ]

    train, val, holdout = split_by_site(examples, holdout=1, val=0)

    assert_disjoint(train, holdout)
    assert {ex.site_url for ex in holdout} <= {"https://a.com", "https://b.com"}
    assert len(train) + len(holdout) == 3
