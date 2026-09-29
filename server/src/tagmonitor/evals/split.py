"""`split`: a deterministic dev/test split, by page.

Each *page* goes to dev or test by a hash of its id and a seed, and all its examples follow
it. Splitting by example instead would let the same page's text sit in both halves, and a
prompt tuned on dev would have partly seen the test set. Hashing also keeps the split stable:
adding examples later never moves an existing one between dev and test.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from tagmonitor.evals.dataset import Example, Split, SplitName, load_split

DEFAULT_SEED = "tag-monitor-message-match"
DEFAULT_TEST_FRACTION = 0.3


class SplitChangeError(ValueError):
    """Refusing to reshuffle an existing split (it would leak test examples into dev)."""


def assign(page_id: str, seed: str, test_fraction: float) -> SplitName:
    bucket = int(hashlib.sha256(f"{seed}:{page_id}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "test" if bucket < test_fraction else "dev"


def make_split(
    examples: list[Example],
    split_path: Path,
    *,
    seed: str = DEFAULT_SEED,
    test_fraction: float = DEFAULT_TEST_FRACTION,
    force: bool = False,
) -> Split:
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be between 0 and 1")
    if split_path.exists() and not force:
        previous = load_split(split_path)
        if (previous.seed, previous.test_fraction) != (seed, test_fraction):
            raise SplitChangeError(
                f"{split_path} was made with seed {previous.seed!r} and test fraction "
                f"{previous.test_fraction}; changing them reshuffles dev and test. "
                "Pass --force only if no prompt has been tuned on the old dev split."
            )
    dev: list[str] = []
    test: list[str] = []
    for example in examples:
        (test if assign(example.page_id, seed, test_fraction) == "test" else dev).append(
            example.example_id
        )
    split = Split(
        seed=seed,
        test_fraction=test_fraction,
        created_at=datetime.now(UTC),
        dev=sorted(dev),
        test=sorted(test),
    )
    split_path.parent.mkdir(parents=True, exist_ok=True)
    split_path.write_text(split.model_dump_json(indent=2) + "\n")
    return split
