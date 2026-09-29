"""Small statistics helpers shared by the research scan and the eval reports."""

import math

Z_95 = 1.959963984540054


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """95% Wilson score interval for a proportion.

    Better than p ± 1.96·SE for small n or proportions near 0 or 1: it never leaves [0, 1]
    and doesn't collapse to zero width at 0/n (Wilson, 1927; Agresti & Coull, 1998).
    """
    if n == 0:
        return (math.nan, math.nan)
    p = successes / n
    denominator = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denominator
    half_width = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denominator
    # Mathematically p is always inside the interval, but at 0/n or n/n floating point can put
    # a bound a hair past p (e.g. a lower bound of 1e-17 above 0), so clamp it back.
    low = min(p, max(0.0, center - half_width))
    high = max(p, min(1.0, center + half_width))
    return (low, high)
