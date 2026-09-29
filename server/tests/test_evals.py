"""The message-match eval tooling (PRD §15): metrics (cross-checked against scikit-learn),
the dataset files, split, label, collect, run and the report.

Every dataset here is written to tmp_path by the test itself: tests never read or write the
real evals/message_match/dataset/, whose labels come from a person (PRD §15).
"""

import json
import math
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sklearn.metrics import cohen_kappa_score  # test-only: the reference implementation
from typer.testing import CliRunner

from tagmonitor.browser.capturer import PageCapturer
from tagmonitor.checks.message_match import AdCopy, PageText
from tagmonitor.db.pool import Pool
from tagmonitor.evals.__main__ import app
from tagmonitor.evals.collect import collect, read_urls
from tagmonitor.evals.dataset import (
    FrozenPage,
    Label,
    LabeledExample,
    append_jsonl,
    fingerprint,
    labeled_examples,
    load_examples,
    load_labels,
    load_pages,
    load_split,
    page_id,
)
from tagmonitor.evals.metrics import (
    bootstrap_kappa_ci,
    compute_metrics,
    confusion_matrix,
    quadratic_weighted_kappa,
)
from tagmonitor.evals.report import render_report, write_report
from tagmonitor.evals.run import (
    SplitRuleViolation,
    check_test_rule,
    load_results,
    save_result,
    score_examples,
    summarize,
)
from tagmonitor.evals.split import SplitChangeError, assign, make_split
from tagmonitor.llm.message_match import MessageMatcher
from tagmonitor.llm.prompts import load_prompt
from tests.fixture_server import FixtureServer
from tests.llm_fakes import FakeTransport, failing, tool_response, verdict
from tests.test_message_match import CONFIG

runner = CliRunner()

# -- metrics -----------------------------------------------------------------------------------


def random_ratings(rng: random.Random, n: int) -> tuple[list[int], list[int]]:
    human = [rng.randint(1, 5) for _ in range(n)]
    # A model that mostly agrees, sometimes off by one or two.
    model = [min(5, max(1, h + rng.choice([0, 0, 0, 1, -1, 2, -2]))) for h in human]
    return human, model


@pytest.mark.parametrize("seed", range(25))
def test_kappa_matches_scikit_learn(seed: int) -> None:
    rng = random.Random(seed)
    human, model = random_ratings(rng, rng.randint(5, 80))
    expected = cohen_kappa_score(human, model, weights="quadratic", labels=[1, 2, 3, 4, 5])
    assert quadratic_weighted_kappa(human, model) == pytest.approx(expected, abs=1e-12)


def test_kappa_uses_the_fixed_scale_even_when_a_score_is_unused() -> None:
    # Nobody used a 3. scikit-learn without labels= would treat 2 and 4 as neighbors.
    human, model = [1, 2, 4, 5, 2, 4], [1, 4, 2, 5, 2, 4]
    fixed = cohen_kappa_score(human, model, weights="quadratic", labels=[1, 2, 3, 4, 5])
    positional = cohen_kappa_score(human, model, weights="quadratic")
    assert quadratic_weighted_kappa(human, model) == pytest.approx(fixed)
    assert fixed != pytest.approx(positional)


def test_kappa_edge_cases() -> None:
    assert quadratic_weighted_kappa([1, 3, 5], [1, 3, 5]) == 1.0
    assert math.isnan(quadratic_weighted_kappa([4, 4], [4, 4]))  # undefined, not "perfect"
    assert math.isnan(quadratic_weighted_kappa([], []))


def test_simple_metrics() -> None:
    human, model = [1, 2, 3, 4, 5], [1, 3, 3, 2, 5]
    metrics = compute_metrics(human, model)
    assert metrics.exact == pytest.approx(3 / 5)
    assert metrics.within_one == pytest.approx(4 / 5)
    assert metrics.mae == pytest.approx(3 / 5)
    assert (metrics.mean_human, metrics.mean_model) == (3.0, 2.8)
    assert metrics.confusion == confusion_matrix(human, model)
    assert metrics.confusion[3] == [0, 1, 0, 0, 0]  # the human's 4 that the model called 2
    assert metrics.exact_ci[0] < 0.6 < metrics.exact_ci[1]


def test_bootstrap_interval_is_seeded_and_brackets_the_estimate() -> None:
    human, model = random_ratings(random.Random(3), 40)
    low, high = bootstrap_kappa_ci(human, model)
    assert (low, high) == bootstrap_kappa_ci(human, model)
    assert low < quadratic_weighted_kappa(human, model) < high


# -- dataset files -----------------------------------------------------------------------------

EXAMPLES_CSV = (
    "﻿url,ad_headline,ad_primary_text,ad_cta\n"
    "https://bakery.example/pies,Fresh pies daily,Order by noon,Order now\n"
    "https://bakery.example/pies,50% off cakes,Today only,Shop now\n"  # same page, other ad
    "https://florist.example/,Wedding flowers,,Get a quote\n"
    "https://gym.example/,Free first class,Join us,Book now\n"
)


def write_examples(dataset: Path, text: str = EXAMPLES_CSV) -> None:
    dataset.mkdir(parents=True, exist_ok=True)
    (dataset / "examples.csv").write_text(text)


def freeze(dataset: Path, url: str, words: str = "Welcome") -> FrozenPage:
    page = FrozenPage(
        page_id=page_id(url),
        url=url,
        final_url=url,
        http_status=200,
        collected_at=datetime(2026, 9, 1, tzinfo=UTC),
        text=PageText(title=words, h1=[words], above_fold_text=f"{words} to our site"),
    )
    append_jsonl(dataset / "pages.jsonl", page)
    return page


def test_examples_file(tmp_path: Path) -> None:
    write_examples(tmp_path)
    examples = load_examples(tmp_path / "examples.csv")
    assert len(examples) == 4
    assert examples[0].page_id == examples[1].page_id != examples[2].page_id
    assert examples[2].ad == AdCopy(headline="Wedding flowers", cta="Get a quote")
    # Ids are content hashes: stable across loads, different when the ad changes.
    assert [e.example_id for e in load_examples(tmp_path / "examples.csv")] == [
        e.example_id for e in examples
    ]
    write_examples(tmp_path, EXAMPLES_CSV.replace("Fresh pies daily", "Fresh pies"))
    assert load_examples(tmp_path / "examples.csv")[0].example_id != examples[0].example_id


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("url,ad_headline\nhttps://a.example/,x\n", "missing column"),
        ("url,ad_headline,ad_primary_text,ad_cta\nhttps://a.example/,,,\n", "no ad copy"),
        (
            "url,ad_headline,ad_primary_text,ad_cta\nhttps://a.example/,x,,\nhttps://a.example/,x,,\n",
            "duplicate",
        ),
    ],
)
def test_examples_file_problems(tmp_path: Path, text: str, error: str) -> None:
    write_examples(tmp_path, text)
    with pytest.raises(ValueError, match=error):
        load_examples(tmp_path / "examples.csv")


def label_for(example_id: str, overall: int, ad: AdCopy, page: FrozenPage) -> Label:
    return Label(
        example_id=example_id,
        overall=overall,
        labeler="tester",
        labeled_at=datetime(2026, 9, 2, tzinfo=UTC),
        ad_fingerprint=fingerprint(ad),
        page_fingerprint=fingerprint(page.text),
    )


def test_labels_go_stale_when_the_page_text_changes(tmp_path: Path) -> None:
    write_examples(tmp_path)
    examples = load_examples(tmp_path / "examples.csv")
    first = examples[0]
    page = freeze(tmp_path, first.url)
    append_jsonl(tmp_path / "labels.jsonl", label_for(first.example_id, 4, first.ad, page))

    usable, stale = labeled_examples(
        examples, load_pages(tmp_path / "pages.jsonl"), load_labels(tmp_path / "labels.jsonl")
    )
    assert [u.example.example_id for u in usable] == [first.example_id] and stale == []

    freeze(tmp_path, first.url, words="Now with a new menu")  # re-collected, text changed
    usable, stale = labeled_examples(
        examples, load_pages(tmp_path / "pages.jsonl"), load_labels(tmp_path / "labels.jsonl")
    )
    assert usable == [] and stale == [first.example_id]


# -- split -------------------------------------------------------------------------------------


def test_split_keeps_a_page_on_one_side_and_is_stable(tmp_path: Path) -> None:
    rows = [
        f"https://site{i}.example/,Offer {i},,Buy\nhttps://site{i}.example/,Other {i},,Buy"
        for i in range(60)
    ]
    write_examples(tmp_path, "url,ad_headline,ad_primary_text,ad_cta\n" + "\n".join(rows))
    examples = load_examples(tmp_path / "examples.csv")
    split = make_split(examples, tmp_path / "split.json")

    side = {eid: "test" for eid in split.test} | {eid: "dev" for eid in split.dev}
    for example in examples:  # both ads for a page land on the same side
        assert side[example.example_id] == assign(example.page_id, split.seed, 0.3)
    assert 0.15 < len(split.test) / len(examples) < 0.45
    assert load_split(tmp_path / "split.json") == split

    # Adding examples later doesn't move existing ones.
    more = [*rows, "https://new.example/,New,,Buy"]
    write_examples(tmp_path, "url,ad_headline,ad_primary_text,ad_cta\n" + "\n".join(more))
    bigger = make_split(load_examples(tmp_path / "examples.csv"), tmp_path / "split.json")
    assert set(split.test) <= set(bigger.test) and set(split.dev) <= set(bigger.dev)


def test_split_refuses_to_reshuffle(tmp_path: Path) -> None:
    write_examples(tmp_path)
    examples = load_examples(tmp_path / "examples.csv")
    make_split(examples, tmp_path / "split.json")
    with pytest.raises(SplitChangeError, match="reshuffles"):
        make_split(examples, tmp_path / "split.json", seed="another")
    make_split(examples, tmp_path / "split.json", seed="another", force=True)


# -- label (interactive) -----------------------------------------------------------------------


def test_label_session_records_answers_and_resumes(tmp_path: Path) -> None:
    write_examples(tmp_path)
    for url in ("https://bakery.example/pies", "https://florist.example/"):
        freeze(tmp_path, url)  # the gym page isn't collected yet
    args = ["label", "--dataset-dir", str(tmp_path), "--labeler", "sam"]

    first = runner.invoke(app, args, input="4\nclear match\ns\nq\n")
    assert first.exit_code == 0, first.output
    assert "run `collect` first" in first.output
    assert "labeled 1, skipped 1, left for later 1" in first.output
    labels = load_labels(tmp_path / "labels.jsonl")
    assert [(label.overall, label.notes, label.labeler) for label in labels.values()] == [
        (4, "clear match", "sam")
    ]

    second = runner.invoke(app, args, input="2\n\n5\n\n")
    assert second.exit_code == 0, second.output
    assert "1 labeled, 2 to go" in second.output
    assert sorted(label.overall for label in load_labels(tmp_path / "labels.jsonl").values()) == [
        2,
        4,
        5,
    ]


def test_relabel_appends_and_the_latest_label_counts(tmp_path: Path) -> None:
    write_examples(tmp_path, "url,ad_headline,ad_primary_text,ad_cta\nhttps://a.example/,x,,\n")
    freeze(tmp_path, "https://a.example/")
    args = ["label", "--dataset-dir", str(tmp_path)]
    runner.invoke(app, args, input="2\n\n")
    (example_id,) = load_labels(tmp_path / "labels.jsonl")
    assert "0 to go" in runner.invoke(app, args).output  # nothing left to label
    result = runner.invoke(app, [*args, "--relabel", example_id], input="3\nsecond look\n")
    assert result.exit_code == 0, result.output
    assert load_labels(tmp_path / "labels.jsonl")[example_id].overall == 3
    assert len((tmp_path / "labels.jsonl").read_text().splitlines()) == 2  # history kept


def test_label_prompt_rejects_other_answers(tmp_path: Path) -> None:
    write_examples(tmp_path, "url,ad_headline,ad_primary_text,ad_cta\nhttps://a.example/,x,,\n")
    freeze(tmp_path, "https://a.example/")
    result = runner.invoke(app, ["label", "--dataset-dir", str(tmp_path)], input="7\nfour\n3\n\n")
    assert result.exit_code == 0, result.output
    assert [label.overall for label in load_labels(tmp_path / "labels.jsonl").values()] == [3]


# -- collect -----------------------------------------------------------------------------------


def test_read_urls_from_text_or_csv(tmp_path: Path) -> None:
    (tmp_path / "urls.txt").write_text("# a comment\nhttps://a.example/\n\nhttps://a.example/\n")
    assert read_urls(tmp_path / "urls.txt") == ["https://a.example/"]
    write_examples(tmp_path)
    assert len(read_urls(tmp_path / "examples.csv")) == 3


async def test_collect_freezes_page_text(
    tmp_path: Path, capturer: PageCapturer, fixture_server: FixtureServer
) -> None:
    urls = [fixture_server.url("meta_ok"), fixture_server.url("http_500"), "http://nowhere.test/"]
    pages_path = tmp_path / "pages.jsonl"
    summary = await collect(urls, pages_path, capturer)

    assert summary.collected == urls[:2]  # an error page still has text worth judging
    assert list(summary.failed) == [urls[2]]
    pages = load_pages(pages_path)
    page = pages[page_id(urls[0])]
    assert page.http_status == 200 and page.text.title

    again = await collect(urls[:1], pages_path, capturer)
    assert again.already_had == urls[:1] and again.collected == []


# -- run and report ----------------------------------------------------------------------------


def labeled_dataset(tmp_path: Path, human: list[int]) -> list[LabeledExample]:
    rows = [f"https://shop{i}.example/,Offer {i},,Buy" for i in range(len(human))]
    write_examples(tmp_path, "url,ad_headline,ad_primary_text,ad_cta\n" + "\n".join(rows))
    examples = load_examples(tmp_path / "examples.csv")
    for example, score in zip(examples, human, strict=True):
        page = freeze(tmp_path, example.url, words=f"Shop {example.url}")
        append_jsonl(
            tmp_path / "labels.jsonl", label_for(example.example_id, score, example.ad, page)
        )
    usable, _ = labeled_examples(
        examples, load_pages(tmp_path / "pages.jsonl"), load_labels(tmp_path / "labels.jsonl")
    )
    return usable


async def test_run_scores_a_split_and_writes_results_and_report(db: Pool, tmp_path: Path) -> None:
    human = [5, 4, 2, 1, 3, 4]
    items = labeled_dataset(tmp_path, human)
    model_score = {item.example.ad.headline: max(1, item.label.overall - 1) for item in items}
    model_score["Offer 5"] = 4  # one exact match on purpose

    def responder(request: dict[str, Any]) -> dict[str, Any]:
        headline = request["messages"][0]["content"].split("<headline>")[1].split("<")[0]
        if headline == "Offer 3":
            raise failing()
        return tool_response(verdict(model_score[headline]), input_tokens=1000, output_tokens=100)

    transport = FakeTransport(responder=responder)
    matcher = MessageMatcher(db, transport, load_prompt("v1"), CONFIG)
    scored = await score_examples(items, matcher, concurrency=3)
    result = summarize(scored, matcher=matcher, split="dev", stale_labels=0, price=(1.0, 5.0))

    assert (result.n_labeled, result.n_scored, result.n_errors) == (6, 5, 1)
    assert result.metrics is not None and result.metrics["n"] == 5
    assert result.input_tokens == 5000 and result.spent_input_tokens == 5000
    assert result.estimated_cost_usd == pytest.approx((5000 * 1.0 + 500 * 5.0) / 1e6)

    results_dir = tmp_path / "results"
    path = save_result(result, results_dir)
    with pytest.raises(FileExistsError):
        save_result(result, results_dir)  # a results file is a record
    assert json.loads(path.read_text())["prompt_version"] == "v1"

    report = write_report(results_dir).read_text()
    assert "## Dev split: prompt versions compared" in report
    assert "| v1 | claude-haiku-4-5-20251001 | 5 |" in report
    assert "Largest disagreements" in report and "Errors:" in report
    assert "Not scored yet" in report  # nothing on test

    # Running again reuses cached answers: same metrics, nothing new spent.
    again = summarize(
        await score_examples(items, matcher, concurrency=3),
        matcher=matcher,
        split="dev",
        stale_labels=0,
        price=(1.0, 5.0),
    )
    assert again.metrics == result.metrics
    assert again.spent_input_tokens == 0 and again.input_tokens == 5000
    assert len(transport.requests) == 6 + 1  # only the failed example was asked again


async def test_test_split_is_scored_once(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    check_test_rule(results_dir, "test", rerun_test=False)  # nothing yet: fine
    (results_dir).mkdir()
    fake = {
        "created_at": "2026-09-20T10:00:00Z",
        "prompt_version": "v2",
        "prompt_sha256": "abc",
        "model": "m",
        "split": "test",
        "n_labeled": 1,
        "n_scored": 1,
        "n_errors": 0,
        "stale_labels": 0,
        "metrics": None,
        "input_tokens": 0,
        "output_tokens": 0,
        "spent_input_tokens": 0,
        "spent_output_tokens": 0,
        "estimated_cost_usd": None,
        "spent_cost_usd": None,
        "examples": [],
    }
    (results_dir / "20260920-100000-v2-test.json").write_text(json.dumps(fake))
    with pytest.raises(SplitRuleViolation, match="already scored"):
        check_test_rule(results_dir, "test", rerun_test=False)
    check_test_rule(results_dir, "dev", rerun_test=False)
    check_test_rule(results_dir, "test", rerun_test=True)

    later = fake | {"created_at": "2026-09-21T10:00:00Z", "prompt_version": "v3"}
    (results_dir / "20260921-100000-v3-test.json").write_text(json.dumps(later))
    report = render_report(load_results(results_dir))
    assert "scored 2 times" in report


def test_report_without_runs() -> None:
    assert "No runs yet." in render_report([])


def test_status_command(tmp_path: Path) -> None:
    write_examples(tmp_path)
    freeze(tmp_path, "https://florist.example/")
    make_split(load_examples(tmp_path / "examples.csv"), tmp_path / "split.json")
    result = runner.invoke(app, ["status", "--dataset-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Examples (ads)" in result.output and "dev:" in result.output
    counts = {
        line.split("│")[1].strip(): line.split("│")[2].strip()
        for line in result.output.splitlines()
        if line.count("│") == 3
    }
    assert counts["Examples (ads)"] == "4"
    assert counts["Distinct pages"] == "3"
    assert counts["Pages collected"] == "1"  # pages, not examples


def test_commands_explain_a_missing_examples_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["status", "--dataset-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "doesn't exist yet" in result.output
