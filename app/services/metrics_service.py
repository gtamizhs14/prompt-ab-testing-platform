from sqlalchemy.orm import Session
from app.db.models import ExperimentRun, ExperimentVariant


def _mean(values: list) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _variance(values: list) -> float | None:
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    return round(sum((x - m) ** 2 for x in values) / (len(values) - 1), 2)


def get_variant_metrics(db: Session, experiment_id: int) -> dict:
    """
    Aggregates raw experiment_runs data into per-variant metrics.

    Returns a dict keyed by variant name. Each entry contains:
    - sample_count
    - latency_ms: mean, variance, and raw values (raw values needed for t-test)
    - error_rate
    - input_tokens / output_tokens: mean
    - quality_score: mean and raw values (for quality-based t-test)

    The raw values lists are stripped from the API response after the t-test
    runs — they're only kept internally for statistical computation.
    """
    variants = (
        db.query(ExperimentVariant)
        .filter(ExperimentVariant.experiment_id == experiment_id)
        .all()
    )

    result = {}

    for variant in variants:
        runs = (
            db.query(ExperimentRun)
            .filter(
                ExperimentRun.experiment_id == experiment_id,
                ExperimentRun.variant == variant.variant_name,
            )
            .all()
        )

        if not runs:
            result[variant.variant_name] = {
                "sample_count": 0,
                "latency_ms": {"mean": None, "variance": None, "values": []},
                "error_rate": None,
                "input_tokens": {"mean": None},
                "output_tokens": {"mean": None},
                "quality_score": {"mean": None, "values": []},
            }
            continue

        latency_values = [r.latency_ms for r in runs if r.latency_ms is not None]
        input_token_values = [r.input_tokens for r in runs if r.input_tokens is not None]
        output_token_values = [r.output_tokens for r in runs if r.output_tokens is not None]
        quality_values = [r.quality_score for r in runs if r.quality_score is not None]
        error_count = sum(1 for r in runs if r.is_error == 1)

        result[variant.variant_name] = {
            "sample_count": len(runs),
            "latency_ms": {
                "mean": _mean(latency_values),
                "variance": _variance(latency_values),
                "values": latency_values,
            },
            "error_rate": round(error_count / len(runs), 4),
            "input_tokens": {"mean": _mean(input_token_values)},
            "output_tokens": {"mean": _mean(output_token_values)},
            "quality_score": {
                "mean": _mean(quality_values),
                "values": quality_values,
            },
        }

    return result


def get_timeseries(db: Session, experiment_id: int, metric: str) -> dict:
    """
    Returns per-variant time-ordered metric values for trend detection.

    metric must be one of: 'latency_ms', 'quality_score', 'input_tokens', 'output_tokens'
    """
    runs = (
        db.query(ExperimentRun)
        .filter(ExperimentRun.experiment_id == experiment_id)
        .order_by(ExperimentRun.created_at.asc())
        .all()
    )

    series: dict[str, list] = {}
    for run in runs:
        value = getattr(run, metric, None)
        if value is None:
            continue
        entry = {"timestamp": run.created_at.isoformat(), "value": value}
        series.setdefault(run.variant, []).append(entry)

    return series
