import math
from scipy import stats

SIGNIFICANCE_LEVEL = 0.05   # 95% confidence threshold
MIN_SAMPLES_PER_VARIANT = 30  # don't run tests below this — results aren't reliable


def compare_variants(
    control_name: str,
    control_values: list[float],
    treatment_name: str,
    treatment_values: list[float],
    metric: str = "latency_ms",
    lower_is_better: bool = True,
) -> dict:
    """
    Runs Welch's t-test between two variant distributions and returns a verdict.

    Why Welch's t-test (equal_var=False):
    A standard t-test assumes both groups have equal variance. In practice,
    variant B (a new prompt) might have a very different latency distribution
    than variant A. Welch's test handles unequal variances — it's more robust
    and almost always the right default for A/B tests.

    Returns a verdict of: 'significant', 'inconclusive', or 'insufficient_data'.
    Also returns confidence_interval, mde, and sample_size_progress.
    """
    n_control = len(control_values)
    n_treatment = len(treatment_values)

    if n_control < MIN_SAMPLES_PER_VARIANT or n_treatment < MIN_SAMPLES_PER_VARIANT:
        needed = max(MIN_SAMPLES_PER_VARIANT - n_control, MIN_SAMPLES_PER_VARIANT - n_treatment, 0)
        return {
            "metric": metric,
            "verdict": "insufficient_data",
            "reason": (
                f"Need at least {MIN_SAMPLES_PER_VARIANT} samples per variant. "
                f"Have {n_control} ({control_name}) and {n_treatment} ({treatment_name})."
            ),
            "p_value": None,
            "winner": None,
            "confidence_interval": None,
            "mde": None,
            "sample_size_progress": {
                "control": {"have": n_control, "need": MIN_SAMPLES_PER_VARIANT},
                "treatment": {"have": n_treatment, "need": MIN_SAMPLES_PER_VARIANT},
                "samples_remaining": needed,
            },
            "control": {"name": control_name, "mean": _safe_mean(control_values), "sample_count": n_control},
            "treatment": {"name": treatment_name, "mean": _safe_mean(treatment_values), "sample_count": n_treatment},
        }

    t_stat, p_value = stats.ttest_ind(control_values, treatment_values, equal_var=False)
    p_value = float(p_value)
    is_significant = bool(p_value < SIGNIFICANCE_LEVEL)

    control_mean = sum(control_values) / n_control
    treatment_mean = sum(treatment_values) / n_treatment

    winner = None
    if is_significant:
        if lower_is_better:
            winner = control_name if control_mean < treatment_mean else treatment_name
        else:
            winner = control_name if control_mean > treatment_mean else treatment_name

    # 95% confidence interval on the difference (control_mean - treatment_mean)
    ci = _confidence_interval(control_values, treatment_values)

    # Minimum detectable effect — smallest difference we can reliably detect at
    # 80% power / 5% significance given current sample sizes and pooled std dev.
    mde = _minimum_detectable_effect(control_values, treatment_values)

    return {
        "metric": metric,
        "verdict": "significant" if is_significant else "inconclusive",
        "p_value": round(p_value, 4),
        "is_significant": is_significant,
        "winner": winner,
        "confidence_level": round(1 - p_value, 4),
        "confidence_interval": ci,
        "mde": mde,
        "sample_size_progress": {
            "control": {"have": n_control, "need": MIN_SAMPLES_PER_VARIANT},
            "treatment": {"have": n_treatment, "need": MIN_SAMPLES_PER_VARIANT},
            "samples_remaining": 0,
        },
        "control": {
            "name": control_name,
            "mean": round(control_mean, 2),
            "sample_count": n_control,
        },
        "treatment": {
            "name": treatment_name,
            "mean": round(treatment_mean, 2),
            "sample_count": n_treatment,
        },
    }


def _safe_mean(values: list) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _confidence_interval(control: list[float], treatment: list[float]) -> dict | None:
    """
    95% confidence interval for the difference in means (control - treatment).

    Welch–Satterthwaite degrees of freedom gives an accurate interval even
    when the two groups have different sample sizes and variances.
    """
    n1, n2 = len(control), len(treatment)
    if n1 < 2 or n2 < 2:
        return None

    m1 = sum(control) / n1
    m2 = sum(treatment) / n2
    s1_sq = sum((x - m1) ** 2 for x in control) / (n1 - 1)
    s2_sq = sum((x - m2) ** 2 for x in treatment) / (n2 - 1)

    se = math.sqrt(s1_sq / n1 + s2_sq / n2)
    if se == 0:
        return {"lower": 0.0, "upper": 0.0, "difference": 0.0}

    # Welch–Satterthwaite degrees of freedom
    df_num = (s1_sq / n1 + s2_sq / n2) ** 2
    df_den = (s1_sq / n1) ** 2 / (n1 - 1) + (s2_sq / n2) ** 2 / (n2 - 1)
    df = df_num / df_den if df_den > 0 else n1 + n2 - 2

    t_crit = stats.t.ppf(0.975, df=df)
    diff = m1 - m2

    return {
        "difference": round(diff, 2),
        "lower": round(diff - t_crit * se, 2),
        "upper": round(diff + t_crit * se, 2),
    }


def _minimum_detectable_effect(control: list[float], treatment: list[float]) -> float | None:
    """
    MDE: the smallest absolute difference we'd have 80% power to detect at α=0.05
    given current pooled standard deviation and sample sizes.

    Formula: MDE = (z_α/2 + z_β) * pooled_std * sqrt(1/n1 + 1/n2)
    where z_α/2 = 1.96 (two-tailed, α=0.05) and z_β = 0.842 (80% power).
    """
    n1, n2 = len(control), len(treatment)
    if n1 < 2 or n2 < 2:
        return None

    m1 = sum(control) / n1
    m2 = sum(treatment) / n2
    var1 = sum((x - m1) ** 2 for x in control) / (n1 - 1)
    var2 = sum((x - m2) ** 2 for x in treatment) / (n2 - 1)

    pooled_std = math.sqrt((var1 * (n1 - 1) + var2 * (n2 - 1)) / (n1 + n2 - 2))
    if pooled_std == 0:
        return 0.0

    z_alpha = 1.96   # α = 0.05, two-tailed
    z_beta = 0.842   # 80% power
    mde = (z_alpha + z_beta) * pooled_std * math.sqrt(1 / n1 + 1 / n2)
    return round(mde, 2)
