"""Read and vet the scan's target list (PRD §16).

data/scan/targets.csv is compiled by hand from public directories (never scraped). Columns:
url, category, source. We load one page per registrable domain, so later rows for a domain
already in the list are skipped, and so are rows the SSRF rules would refuse or whose host
isn't a domain name (a typo or an IP address is a data-entry problem, not an unreachable
business, so it must not count against the reachability rate).
"""

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from tagmonitor.browser.ssrf import SsrfError, SsrfPolicy, validate_user_url
from tagmonitor.urls import registrable_domain

REQUIRED_COLUMNS = {"url", "category", "source"}
_DNS_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


@dataclass(frozen=True)
class Target:
    url: str
    category: str
    source: str
    domain: str
    skip_reason: str | None = None  # set when the row won't be loaded


def load_targets(path: Path, policy: SsrfPolicy) -> list[Target]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} is missing column(s): {', '.join(sorted(missing))}")
        rows = list(reader)

    targets: list[Target] = []
    seen_domains: set[str] = set()
    for row in rows:
        url = (row["url"] or "").strip()
        if not url:
            continue
        if "://" not in url:
            url = f"https://{url}"  # directories often list bare domains
        category = (row["category"] or "").strip() or "uncategorized"
        source = (row["source"] or "").strip()
        try:
            parts = validate_user_url(url, policy)
        except SsrfError:
            parts = None
        if parts is None or not is_domain_name(parts.hostname or ""):
            targets.append(Target(url, category, source, "", skip_reason="invalid_url"))
            continue
        domain = registrable_domain(url)
        if domain in seen_domains:
            targets.append(Target(url, category, source, domain, skip_reason="duplicate_domain"))
            continue
        seen_domains.add(domain)
        targets.append(Target(url, category, source, domain))
    return targets


def is_domain_name(host: str) -> bool:
    """A syntactically valid DNS name with at least two labels (IDNs allowed, IPs not)."""
    try:
        ascii_host = host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return False
    labels = ascii_host.split(".")
    if len(labels) < 2 or len(ascii_host) > 253 or labels[-1].isdigit():
        return False  # an all-numeric last label means an IPv4 address, never a TLD
    return all(_DNS_LABEL.match(label) for label in labels)
