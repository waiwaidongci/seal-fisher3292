"""Exact group-sequential calibration for pre-registered two-arm seal trials.

Under the null hypothesis both processes share one success probability
``p in [0, 1]``.  At look ``k`` the cumulative 2x2 table is judged with the
two-sided Fisher exact test; the trial stops at the first look whose p-value is
at most that look's pre-registered threshold.

Because both arms are independent and every look fixes the cumulative sample
size per arm, the probability of stopping at look ``k`` is a polynomial in
``p`` with rational coefficients.  ``F(p)`` is their sum, the exact type-I
error at a common success rate ``p``.  This module:

* builds ``F`` in exact rational arithmetic from the look schedule;
* certifies ``max_{p in [0,1]} F(p)`` with rational Bernstein enclosures;
* decides exactly whether that maximum is strictly greater than the total limit
  with a Sturm root-isolation sign test.

No sampling grid is used, and no sum of per-look thresholds is used, either for
the maximum or for the enable/disable decision.
"""
from __future__ import annotations

import heapq
import math
from fractions import Fraction
from functools import lru_cache
from math import comb

MAX_GROUP = 12
MAX_LOOKS = 3
MAX_SPLITS = 20000
CERTIFY_TOLERANCE = Fraction(1, 10**12)


def as_fraction(value) -> Fraction:
    """Parse a threshold exactly (decimal text, integer, or rational text)."""
    if isinstance(value, bool):
        raise ValueError("阈值必须是数值")
    if isinstance(value, Fraction):
        out = value
    elif isinstance(value, int):
        out = Fraction(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("阈值必须为有限数值")
        out = Fraction(repr(value))
    elif isinstance(value, str):
        try:
            out = Fraction(value.strip())
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError("阈值必须是有限数值") from exc
    else:
        raise ValueError("阈值必须是数值")
    if not math.isfinite(float(out)):
        raise ValueError("阈值必须为有限数值")
    return out


def fitted_count(value) -> int:
    if isinstance(value, bool):
        raise ValueError("计数须为非负整数")
    if isinstance(value, Fraction):
        if value.denominator != 1:
            raise ValueError("计数须为非负整数")
        value = value.numerator
    if not isinstance(value, int):
        raise ValueError("计数须为非负整数")
    return value


@lru_cache(maxsize=16384)
def fisher_two_sided_exact(a: int, b: int, c: int, d: int) -> Fraction:
    """Exact two-sided Fisher p-value with fixed margins.

    Sums the hypergeometric probability of every 2x2 table whose probability is
    no greater than the observed table's probability, matching the project's
    stated method and the fixed-margin oracle.
    """
    a, b, c, d = (fitted_count(x) for x in (a, b, c, d))
    if min(a, b, c, d) < 0:
        raise ValueError("计数须为非负整数")
    n1, n2 = a + b, c + d
    if n1 == 0 or n2 == 0:
        raise ValueError("两组各需至少1份试件")
    successes = a + c
    total = n1 + n2
    lo, hi = max(0, successes - n2), min(n1, successes)
    denominator = comb(total, n1)
    numerators = {
        x: comb(successes, x) * comb(total - successes, n1 - x)
        for x in range(lo, hi + 1)
    }
    observed = numerators[a]
    return Fraction(sum(n for n in numerators.values() if n <= observed), denominator)


def exact_text(value: Fraction) -> str:
    """Exact human-readable text: shortest decimal when terminating, else a ratio."""
    value = Fraction(value)
    sign = "-" if value < 0 else ""
    value = abs(value)
    numerator, denominator = value.numerator, value.denominator
    twos = fives = 0
    rest = denominator
    while rest % 2 == 0:
        rest //= 2
        twos += 1
    while rest % 5 == 0:
        rest //= 5
        fives += 1
    if rest != 1:
        return f"{sign}{numerator}/{denominator}"
    digits = max(twos, fives)
    scaled = numerator * 10**digits // denominator
    text = str(scaled).rjust(digits + 1, "0")
    if digits == 0:
        return sign + text
    text = text[:-digits] + "." + text[-digits:]
    text = text.rstrip("0").rstrip(".")
    return sign + text


def normalize_looks(looks) -> list[dict]:
    """Validate and canonicalise the pre-registered cumulative look schedule."""
    if not isinstance(looks, (list, tuple)) or not looks:
        raise ValueError("至少需要1次查看")
    if len(looks) > MAX_LOOKS:
        raise ValueError(f"至多{MAX_LOOKS}次查看")
    out, previous = [], 0
    for index, item in enumerate(looks, start=1):
        if isinstance(item, dict):
            n, alpha = item.get("n"), item.get("alpha")
        else:
            n, alpha = getattr(item, "n", None), getattr(item, "alpha", None)
        if isinstance(n, bool) or not isinstance(n, int):
            raise ValueError("累计样本量必须是整数")
        if n < 1:
            raise ValueError("累计样本量必须为正")
        if n > MAX_GROUP:
            raise ValueError(f"每组累计样本量至多{MAX_GROUP}件")
        if n <= previous:
            raise ValueError("累计样本量必须逐次严格递增")
        alpha_value = as_fraction(alpha)
        if not 0 < alpha_value < 1:
            raise ValueError("各次阈值必须在0和1之间")
        out.append(
            {
                "look": index,
                "n": n,
                "delta": n - previous,
                "alpha": exact_text(alpha_value),
            }
        )
        previous = n
    return out


def normalize_total_alpha(value) -> Fraction:
    alpha = as_fraction(value)
    if not 0 < alpha < 1:
        raise ValueError("总限额必须在0和1之间")
    return alpha


# ---------------------------------------------------------------------------
# Exact rejection polynomial
# ---------------------------------------------------------------------------

@lru_cache(maxsize=4096)
def _crossing_flags(n: int, alpha_key: str) -> tuple[tuple[bool, ...], ...]:
    alpha = as_fraction(alpha_key)
    return tuple(
        tuple(fisher_two_sided_exact(a, n - a, b, n - b) <= alpha for b in range(n + 1))
        for a in range(n + 1)
    )


@lru_cache(maxsize=512)
def type1_polynomial_key(looks_key: tuple) -> tuple:
    """Coefficients (low to high) of the exact rejection probability polynomial.

    A path that first crosses at look ``k`` contributes the binomial prefix
    weight of looks ``1..k`` only; later increments never happen, because the
    trial has already stopped.  Stopping at look ``k`` adds
    ``w_a * w_b * p^s (1-p)^(2 n_k - s)`` where ``s`` is the cumulative number of
    successes in both arms at look ``k``.
    """
    sizes = [n for n, _ in looks_key]
    flags = [_crossing_flags(n, alpha) for n, alpha in looks_key]
    degree = 2 * sizes[-1]
    polynomial = [Fraction(0)] * (degree + 1)

    def walk(index, previous, arm_a, weight_a, arm_b, weight_b):
        n = sizes[index]
        delta = n - previous
        final = index == len(sizes) - 1
        for incremented_a in range(delta + 1):
            next_a = arm_a + incremented_a
            next_weight_a = weight_a * comb(delta, incremented_a)
            for incremented_b in range(delta + 1):
                next_b = arm_b + incremented_b
                next_weight_b = weight_b * comb(delta, incremented_b)
                if flags[index][next_a][next_b]:
                    successes = next_a + next_b
                    remaining = 2 * n - successes
                    product = next_weight_a * next_weight_b
                    for j in range(remaining + 1):
                        polynomial[successes + j] += product * comb(remaining, j) * (-1) ** j
                elif not final:
                    walk(index + 1, n, next_a, next_weight_a, next_b, next_weight_b)

    walk(0, 0, 0, 1, 0, 1)
    return tuple(_trim(polynomial))


def type1_polynomial(looks) -> list[Fraction]:
    normalized = normalize_looks(looks)
    return list(
        type1_polynomial_key(tuple((item["n"], item["alpha"]) for item in normalized))
    )


def type1_probability(looks, p) -> Fraction:
    """Exact type-I error at a given common success rate p."""
    return _eval(type1_polynomial(looks), as_fraction(p))


# ---------------------------------------------------------------------------
# Exact polynomial helpers
# ---------------------------------------------------------------------------

def _trim(poly):
    poly = [Fraction(x) for x in poly]
    while poly and poly[-1] == 0:
        poly.pop()
    return poly


def _eval(poly, x: Fraction) -> Fraction:
    total = Fraction(0)
    for coefficient in reversed(poly):
        total = total * x + coefficient
    return total


def _derivative(poly):
    return _trim([poly[i] * i for i in range(1, len(poly))])


def _scale_positive(poly):
    """Scale by a positive rational so coefficients are coprime integers.

    Unlike a canonical primitive part this never flips the leading sign, which
    Sturm sequences require.
    """
    poly = _trim(poly)
    if not poly:
        return []
    denominator = 1
    for value in poly:
        denominator = math.lcm(denominator, value.denominator)
    integers = [int(value * denominator) for value in poly]
    divisor = 0
    for value in integers:
        divisor = math.gcd(divisor, abs(value))
    if divisor == 0:
        return []
    return [Fraction(value // divisor) for value in integers]


def _divmod_poly(dividend, divisor):
    dividend = _trim(dividend)
    divisor = _trim(divisor)
    if not divisor:
        raise ZeroDivisionError("polynomial division by zero")
    degree = len(divisor) - 1
    quotient = [Fraction(0)] * max(0, len(dividend) - degree)
    while dividend and len(dividend) - 1 >= degree:
        factor = dividend[-1] / divisor[-1]
        shift = len(dividend) - 1 - degree
        quotient[shift] = factor
        for i in range(degree + 1):
            dividend[i + shift] -= factor * divisor[i]
        dividend = _trim(dividend)
    return _trim(quotient), dividend


def _gcd_poly(left, right):
    left, right = _scale_positive(left), _scale_positive(right)
    while right:
        _, remainder = _divmod_poly(left, right)
        left, right = right, _scale_positive(remainder)
    if left and left[-1] < 0:
        left = [-value for value in left]
    return left


def _squarefree_distinct_roots(poly):
    """Squarefree part: same distinct roots, all simple."""
    poly = _scale_positive(poly)
    if len(poly) <= 1:
        return poly
    common = _gcd_poly(poly, _derivative(poly))
    if len(common) <= 1:
        return poly
    quotient, _ = _divmod_poly(poly, common)
    return _scale_positive(quotient)


def _sturm_sequence(poly):
    poly = _scale_positive(poly)
    sequence = [poly]
    if len(poly) <= 1:
        return sequence
    sequence.append(_scale_positive(_derivative(poly)))
    while len(sequence[-1]) > 1:
        _, remainder = _divmod_poly(sequence[-2], sequence[-1])
        if not remainder:
            break
        sequence.append(_scale_positive([-value for value in remainder]))
    return sequence


def _variations(sequence, point: Fraction) -> int:
    signs = []
    for poly in sequence:
        value = _eval(poly, point)
        if value:
            signs.append(1 if value > 0 else -1)
    return sum(1 for left, right in zip(signs, signs[1:]) if left != right)


def _non_root_point(left: Fraction, right: Fraction, poly) -> Fraction:
    """A rational strictly inside (left, right) that is not a root of poly."""
    for step in range(3, 15):
        point = left + (right - left) * Fraction(step, 16)
        if _eval(poly, point) != 0:
            return point
    return (left + right) / 2


def _remove_endpoint_roots(poly):
    poly = _scale_positive(poly)
    while poly and _eval(poly, Fraction(0)) == 0:
        poly, _ = _divmod_poly(poly, [Fraction(0), Fraction(1)])
        poly = _scale_positive(poly)
    while poly and _eval(poly, Fraction(1)) == 0:
        poly, _ = _divmod_poly(poly, [Fraction(-1), Fraction(1)])
        poly = _scale_positive(poly)
    return poly


def _isolation_intervals(poly):
    """Disjoint rational intervals, each holding exactly one distinct root in (0,1).

    ``poly`` must be squarefree with no root at 0 or 1.  Each root lies strictly
    inside its interval; intervals are sorted and do not touch.
    """
    poly = _scale_positive(poly)
    if len(poly) <= 1:
        return []
    sequence = _sturm_sequence(poly)
    count = _variations(sequence, Fraction(0)) - _variations(sequence, Fraction(1))
    if count <= 0:
        return []
    pending = [(Fraction(0), Fraction(1), count)]
    intervals = []
    guard = 0
    while pending:
        guard += 1
        if guard > 500000:
            raise ArithmeticError("根隔离未收敛")
        left, right, held = pending.pop()
        if held <= 0:
            continue
        if held == 1:
            intervals.append((left, right))
            continue
        middle = _non_root_point(left, right, poly)
        left_count = _variations(sequence, left) - _variations(sequence, middle)
        pending.append((left, middle, left_count))
        pending.append((middle, right, held - left_count))
    intervals.sort()
    return intervals


def _shrink_away_from_endpoints(poly, left, right):
    """Tighten one simple-root interval so that 0 < left and right < 1."""
    sequence = _sturm_sequence(poly)
    while left == 0:
        middle = right / 2
        if _variations(sequence, Fraction(0)) - _variations(sequence, middle) == 0:
            left = middle
        else:
            right = middle
    while right == 1:
        middle = (1 + left) / 2
        if _variations(sequence, middle) - _variations(sequence, Fraction(1)) == 0:
            right = middle
        else:
            left = middle
    return left, right


def _exists_strictly_positive(poly) -> bool:
    """Exactly decide whether some p in [0,1] has poly(p) > 0.

    The squarefree part isolates every distinct root in (0,1).  The sign of the
    polynomial is constant on each open gap between those roots, so sampling one
    rational point from every gap (and from the two end gaps) is conclusive.
    """
    poly = _trim(poly)
    if not poly:
        return False
    if _eval(poly, Fraction(0)) > 0 or _eval(poly, Fraction(1)) > 0:
        return True
    squarefree = _remove_endpoint_roots(_squarefree_distinct_roots(poly))
    if not squarefree or len(squarefree) <= 1:
        return _eval(poly, Fraction(1, 2)) > 0
    intervals = [
        _shrink_away_from_endpoints(squarefree, left, right)
        for left, right in _isolation_intervals(squarefree)
    ]
    if not intervals:
        return _eval(poly, Fraction(1, 2)) > 0
    samples = [intervals[0][0] / 2]
    for (_, upper), (next_lower, _) in zip(intervals, intervals[1:]):
        samples.append((upper + next_lower) / 2)
    samples.append((intervals[-1][1] + 1) / 2)
    return any(_eval(poly, point) > 0 for point in samples)


# ---------------------------------------------------------------------------
# Certified maximum (display) and exact gate (decision)
# ---------------------------------------------------------------------------

def _bernstein_coefficients(poly, left: Fraction, right: Fraction):
    degree = len(poly) - 1
    if left == 0 and right == 1:
        shifted = [Fraction(x) for x in poly]
    else:
        width = right - left
        shifted = [Fraction(0)] * (degree + 1)
        for i, coefficient in enumerate(poly):
            if coefficient == 0:
                continue
            for j in range(i + 1):
                shifted[j] += coefficient * comb(i, j) * left ** (i - j) * width ** j
    out = []
    for k in range(degree + 1):
        total = Fraction(0)
        for i in range(k + 1):
            total += shifted[i] * Fraction(comb(k, i), comb(degree, i))
        out.append(total)
    return out


def certified_max(poly, tolerance: Fraction = CERTIFY_TOLERANCE, max_splits: int = MAX_SPLITS):
    """Rigorous rational lower/upper bounds on max_{p in [0,1]} poly(p)."""
    poly = _trim(poly)
    if not poly:
        return Fraction(0), Fraction(0), Fraction(1, 2), 0, True
    left_value, right_value = _eval(poly, Fraction(0)), _eval(poly, Fraction(1))
    lower = max(left_value, right_value)
    best = Fraction(1) if right_value >= left_value else Fraction(0)
    initial = _bernstein_coefficients(poly, Fraction(0), Fraction(1))
    upper = max(initial)
    heap = [(-upper, 0, Fraction(0), Fraction(1))]
    counter = 1
    splits = 0
    while upper - lower > tolerance and splits < max_splits:
        if not heap:
            break
        negative, _, left, right = heapq.heappop(heap)
        if -negative <= lower:
            continue
        middle = (left + right) / 2
        value = _eval(poly, middle)
        if value > lower:
            lower, best = value, middle
        for sub_left, sub_right in ((left, middle), (middle, right)):
            coefficients = _bernstein_coefficients(poly, sub_left, sub_right)
            heapq.heappush(heap, (-max(coefficients), counter, sub_left, sub_right))
            counter += 1
        splits += 1
        upper = max(-item[0] for item in heap) if heap else lower
    if upper < lower:
        upper = lower
    return lower, upper, best, splits, upper - lower <= tolerance


def threshold_sum(looks) -> Fraction:
    """Only a contrasting reference; never used for the enable decision."""
    return sum((as_fraction(item["alpha"]) for item in normalize_looks(looks)), Fraction(0))


def validation_report(looks, total_alpha, tolerance: Fraction = CERTIFY_TOLERANCE) -> dict:
    """Pre-enable exact calibration report for one schedule."""
    normalized = normalize_looks(looks)
    limit = normalize_total_alpha(total_alpha)
    looks_key = tuple((item["n"], item["alpha"]) for item in normalized)
    polynomial = list(type1_polynomial_key(looks_key))
    lower, upper, best, splits, certified = certified_max(polynomial, tolerance)
    difference = list(polynomial) or [Fraction(0)]
    difference[0] -= limit
    over = _exists_strictly_positive(difference)
    sample_sum = sum((as_fraction(item["alpha"]) for item in normalized), Fraction(0))
    return {
        "looks": normalized,
        "total_alpha": exact_text(limit),
        "total_alpha_value": float(limit),
        "max_type1": float((lower + upper) / 2),
        "max_type1_lower": format(float(lower), ".17g"),
        "max_type1_upper": format(float(upper), ".17g"),
        "max_type1_lower_value": float(lower),
        "max_type1_upper_value": float(upper),
        "certified_width": float(upper - lower),
        "certified_tolerance": format(float(tolerance), ".17g"),
        "certified": certified,
        "maximizer_p": float(best),
        "threshold_sum": exact_text(sample_sum),
        "threshold_sum_value": float(sample_sum),
        "threshold_sum_used_for_decision": False,
        "exceeds_limit": over,
        "within_limit": not over,
        "decision": "fail" if over else "pass",
        "method": "exact_rational_polynomial_bernstein_sturm",
        "note": (
            "以有理数精确构造共同合格率 p 的拒绝概率多项式，用 Bernstein 有理包围求最大值界，"
            "并以 Sturm 根隔离精确判定是否严格超过总限额；未使用网格抽样，"
            "也未用各次阈值相加代替。"
        ),
        "splits": splits,
    }
