"""Agreement between the model's overall score and the human label, on the fixed 1-5 scale.

Written out by hand (it's short) and cross-checked against scikit-learn in the tests.
One subtlety worth knowing: scikit-learn's cohen_kappa_score weighs disagreements by the
*position* of each label among the labels it saw, so if nobody used a 3, a 2-vs-4
disagreement would count as one step. Here the scale is always 1..5.
"""

import math
import random
from dataclasses import asdict, dataclass
from typing import Any

from tagmonitor.stats import wilson_interval

SCALE = (1, 2, 3, 4, 5)


def confusion_matrix(human: list[int], model: list[int]) -> list[list[int]]:
    """Rows are the human's score, columns the model's."""
    matrix = [[0] * len(SCALE) for _ in SCALE]
    for h, m in zip(human, model, strict=True):
        matrix[SCALE.index(h)][SCALE.index(m)] += 1
    return matrix


def quadratic_weighted_kappa(human: list[int], model: list[int]) -> float:
    """Cohen's kappa with quadratic weights: 1 = perfect agreement, 0 = what chance would
    give with the same score distributions. Disagreeing by two points costs four times as
    much as disagreeing by one. NaN when undefined (both raters gave one identical score)."""
    n = len(human)
    if n == 0:
        return math.nan
    observed = confusion_matrix(human, model)
    human_totals = [sum(row) for row in observed]
    model_totals = [sum(row[j] for row in observed) for j in range(len(SCALE))]
    k = len(SCALE) - 1
    disagreement = expected = 0.0
    for i in range(len(SCALE)):
        for j in range(len(SCALE)):
            weight = (i - j) ** 2 / k**2
            disagreement += weight * observed[i][j]
            expected += weight * human_totals[i] * model_totals[j] / n
    return math.nan if expected == 0 else 1 - disagreement / expected


@dataclass(frozen=True)
class Metrics:
    n: int
    exact: float
    exact_ci: tuple[float, float]
    within_one: float
    within_one_ci: tuple[float, float]
    kappa: float
    kappa_ci: tuple[float, float]  # bootstrap percentile interval
    mae: float
    mean_human: float
    mean_model: float  # compare with mean_human: is the model harsher or kinder?
    confusion: list[list[int]]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def bootstrap_kappa_ci(
    human: list[int], model: list[int], *, resamples: int = 2000, seed: int = 7311
) -> tuple[float, float]:
    """95% percentile bootstrap interval for kappa. Seeded, so a report is reproducible.
    With the few dozen examples a hand-labeled set has, this interval is wide: report it."""
    n = len(human)
    if n < 2:
        return (math.nan, math.nan)
    rng = random.Random(seed)  # noqa: S311 (statistics, not security)
    values = []
    for _ in range(resamples):
        picks = [rng.randrange(n) for _ in range(n)]
        value = quadratic_weighted_kappa([human[i] for i in picks], [model[i] for i in picks])
        if not math.isnan(value):
            values.append(value)
    if len(values) < resamples // 2:
        return (math.nan, math.nan)
    values.sort()
    return (values[int(0.025 * len(values))], values[int(0.975 * len(values)) - 1])


def compute_metrics(human: list[int], model: list[int]) -> Metrics:
    n = len(human)
    if n != len(model):
        raise ValueError("human and model scores must pair up")
    exact = sum(h == m for h, m in zip(human, model, strict=True))
    within = sum(abs(h - m) <= 1 for h, m in zip(human, model, strict=True))
    return Metrics(
        n=n,
        exact=exact / n if n else math.nan,
        exact_ci=wilson_interval(exact, n),
        within_one=within / n if n else math.nan,
        within_one_ci=wilson_interval(within, n),
        kappa=quadratic_weighted_kappa(human, model),
        kappa_ci=bootstrap_kappa_ci(human, model),
        mae=sum(abs(h - m) for h, m in zip(human, model, strict=True)) / n if n else math.nan,
        mean_human=sum(human) / n if n else math.nan,
        mean_model=sum(model) / n if n else math.nan,
        confusion=confusion_matrix(human, model),
    )
