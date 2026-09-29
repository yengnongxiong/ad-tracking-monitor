# Message match evals

How well does the model's message-match score agree with a person's? This folder holds everything needed to measure it:

- `prompts/`: the prompt versions (`v1.md`, `v2.md`, and so on).
- `dataset/`: your ads, the frozen page text, your labels, and the dev/test split.
- `results/`: one JSON file per run, plus `report.md` comparing them.

The labels are yours. The tools only record the answers you give them.

## 1. Write the examples: `dataset/examples.csv`

One row per ad:

```csv
url,ad_headline,ad_primary_text,ad_cta
https://www.example-bakery.com/pies,Fresh pies baked daily,Order by noon for same-day pickup,Order now
https://www.example-bakery.com/pies,50% off wedding cakes,This month only,Get a quote
```

- **Mix good and bad matches.** A set where every ad fits its page can't show whether the model notices a mismatch. A good way to get both: write two ads for the same page, one that fits and one that promises something the page doesn't deliver.
- **Aim for about 50 examples** from at least 25 different pages, so the test split has enough pages to mean something.
- **Pages.** They can be your own, clients' (with permission), or public business pages. The collected page text ends up in `dataset/pages.jsonl`. If the repo is public, that text is published too, so leave out pages you wouldn't want quoted, or keep `dataset/` out of git.
- **Ids.** An example's id is a hash of its URL and ad text. If you edit an ad, it becomes a new example that needs a new label.

## 2. Collect the pages

```bash
make eval-collect      # or: cd server && uv run python -m tagmonitor.evals collect
```

This loads each distinct URL once, on mobile, with the same browser and SSRF guard as monitoring. It saves the text a phone shows before scrolling to `dataset/pages.jsonl`: title, meta description, headings, visible text, and buttons. The model and you will both judge exactly this frozen text, even if the live page changes later.

Pages that fail to load aren't saved; run it again to retry them. `--refresh` re-collects every page. If a page's text changed, any label for it becomes stale and `label` asks for it again.

## 3. Label

```bash
make eval-label        # interactive; run it in a real terminal
```

For each example you see the ad, the page text, and this rubric. You type an overall score from 1 to 5 and an optional note.

| Score | Meaning |
|---|---|
| 5 | Seamless: the offer and the action the ad promised, right away. |
| 4 | Clearly the right page, with small gaps (a detail below the fold, a differently worded button). |
| 3 | Related, but the visitor has to work to connect it to the ad. |
| 2 | Mostly a mismatch: a generic page, or the promised offer isn't visible. |
| 1 | Unrelated or contradictory: another offer, a conflicting price, an error or an empty page. |

- **Blind:** you never see the model's score, so it can't anchor you.
- **Resumable:** each answer is saved right away. `s` skips an example and `q` quits.
- **Shuffled:** two ads for the same page don't come up back to back.

The rubric is the same one the prompt gives the model (`prompts/v1.md`). If you change what the scores mean, change both.

## 4. Split

```bash
make eval-split
```

This makes a deterministic 70/30 dev/test split, **by page**: every ad for a page goes to the same side. That way a prompt tuned on dev hasn't seen any test page's text. Adding examples later never moves existing ones between dev and test, and the command refuses to reshuffle with a different seed unless you pass `--force`.

`make eval-status` shows how many examples each side has and how your labels are spread.

## 5. Run

Put `ANTHROPIC_API_KEY` in `.env` first, with `make dev` running (the cache and the daily budget live in Postgres).

```bash
make eval-run PROMPT=v1 SPLIT=dev
```

Each run writes `results/<time>-<prompt>-<split>.json` and regenerates `results/report.md`. The report shows:

- exact and within-one agreement, with 95% Wilson intervals
- quadratic-weighted Cohen's kappa, with a seeded 95% bootstrap interval
- MAE, and the mean human score next to the mean model score (is the model harsher or kinder?)
- the confusion matrix
- the largest disagreements, with the model's first issue and your note
- token cost

Answers are cached, keyed on the model, the prompt text, the ad and the page text. Re-running a prompt on the same examples is free and gives the same numbers. The report still shows what the answers cost when they were first made.

## The process

1. **Iterate on dev only.** Copy `prompts/v1.md` to `v2.md`, change it, and run `make eval-run PROMPT=v2 SPLIT=dev`. Compare the two in `report.md`. Read the disagreements: sometimes the prompt is wrong, and sometimes the label deserves a second look. `python -m tagmonitor.evals label --relabel <example id>` asks again; the new label is appended and the latest one counts. Do this before you score test, never because of test results.
2. **Score test once**, with the final prompt: `make eval-run PROMPT=v2 SPLIT=test`. The command refuses to score test a second time without `--rerun-test`, and the report flags every extra test run. The first test run is the number to quote.
3. **Point monitoring at the winner** by setting `LLM_PROMPT_VERSION=v2` in `.env`.

With about 15 test examples, the intervals will be wide. The report says so. Quote the number with its interval.
