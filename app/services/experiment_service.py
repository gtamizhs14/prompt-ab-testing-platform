from sqlalchemy.orm import Session
from app.db.models import Experiment, ExperimentVariant, ExperimentRun, Prompt, PromptVersion
from app.services.hash_service import assign_variant

ERROR_RATE_THRESHOLD = 0.10
ERROR_RATE_WINDOW = 20

# Performance-based auto-stop: if the losing variant is significantly worse
# on the primary metric and we have enough data, cancel the experiment.
PERF_SIGNIFICANCE_LEVEL = 0.01  # stricter threshold for auto-stop than manual review


def get_active_experiment_for_prompt(db: Session, prompt_id: int) -> Experiment | None:
    return (
        db.query(Experiment)
        .filter(Experiment.prompt_id == prompt_id, Experiment.status == "running")
        .first()
    )


def resolve_prompt_version(db: Session, prompt_id: int, user_id: str):
    """
    Returns the prompt version to use and experiment context (if any).

    If a running experiment exists for this prompt, the user is assigned to a
    variant via consistent hashing — same user always gets the same variant.
    Otherwise, the prompt's active version is used.
    """
    experiment = get_active_experiment_for_prompt(db, prompt_id)

    if experiment:
        variants = (
            db.query(ExperimentVariant)
            .filter(ExperimentVariant.experiment_id == experiment.id)
            .all()
        )
        variant_list = [
            {"name": v.variant_name, "traffic": v.traffic_percentage}
            for v in variants
        ]
        assigned_name = assign_variant(user_id, variant_list)
        selected = next((v for v in variants if v.variant_name == assigned_name), None)

        if not selected:
            return None, None, None

        version = db.query(PromptVersion).filter_by(id=selected.prompt_version_id).first()
        return version, experiment.id, assigned_name

    # No active experiment — use the prompt's pinned active version
    prompt = db.query(Prompt).filter_by(id=prompt_id).first()
    if not prompt or not prompt.active_version_id:
        return None, None, None

    version = db.query(PromptVersion).filter_by(id=prompt.active_version_id).first()
    return version, None, None


def log_run(
    db: Session,
    experiment_id: int,
    user_id: str,
    variant: str,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
    is_error: bool,
    response_text: str,
) -> ExperimentRun:
    run = ExperimentRun(
        experiment_id=experiment_id,
        user_id=user_id,
        variant=variant,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        is_error=1 if is_error else 0,
        response_text=response_text,
    )
    db.add(run)
    db.commit()
    return run


def check_and_apply_auto_stop(db: Session, experiment_id: int, variant: str) -> bool:
    """
    Look at the last ERROR_RATE_WINDOW runs for this variant.
    If error rate exceeds the threshold, cancel the experiment and return True.
    """
    recent_runs = (
        db.query(ExperimentRun)
        .filter(
            ExperimentRun.experiment_id == experiment_id,
            ExperimentRun.variant == variant,
        )
        .order_by(ExperimentRun.created_at.desc())
        .limit(ERROR_RATE_WINDOW)
        .all()
    )

    if len(recent_runs) < ERROR_RATE_WINDOW:
        return False  # not enough data yet

    error_count = sum(1 for r in recent_runs if r.is_error == 1)
    error_rate = error_count / len(recent_runs)

    if error_rate > ERROR_RATE_THRESHOLD:
        _cancel_experiment(db, experiment_id)
        return True

    return False


def check_performance_auto_stop(db: Session, experiment_id: int) -> bool:
    """
    Performance-based auto-stop: if one variant is statistically significantly
    worse than the other at a strict threshold (p < 0.01), cancel the experiment
    to protect users from a clearly inferior prompt.

    Only triggers after MIN_SAMPLES_PER_VARIANT runs per variant.
    """
    from app.services import metrics_service, stats_service

    experiment = db.query(Experiment).filter_by(id=experiment_id).first()
    if not experiment or experiment.status != "running":
        return False

    variant_metrics = metrics_service.get_variant_metrics(db, experiment_id)
    if len(variant_metrics) < 2:
        return False

    variant_names = list(variant_metrics.keys())
    control_name, treatment_name = variant_names[0], variant_names[1]
    primary_metric = experiment.primary_metric or "latency_ms"

    from app.api.experiment_routes import METRIC_CONFIG
    metric_key, lower_is_better = METRIC_CONFIG.get(primary_metric, ("latency_ms", True))

    control_values = variant_metrics[control_name][metric_key]["values"]
    treatment_values = variant_metrics[treatment_name][metric_key]["values"]

    result = stats_service.compare_variants(
        control_name=control_name,
        control_values=control_values,
        treatment_name=treatment_name,
        treatment_values=treatment_values,
        metric=primary_metric,
        lower_is_better=lower_is_better,
    )

    p_value = result.get("p_value")
    winner = result.get("winner")

    if p_value is not None and p_value < PERF_SIGNIFICANCE_LEVEL and winner:
        _cancel_experiment(db, experiment_id)
        return True

    return False


def declare_winner_if_significant(db: Session, experiment_id: int) -> bool:
    """
    Soft winner declaration: if p < 0.05 on the primary metric, record the
    winning variant on the experiment and log a notification to the owner.

    Distinct from performance auto-stop (p < 0.01 + cancels): this just
    marks the winner and leaves the experiment running so the team can
    manually decide whether to promote or keep collecting data.
    """
    from app.services import metrics_service, stats_service
    from app.api.experiment_routes import METRIC_CONFIG

    WINNER_SIGNIFICANCE_LEVEL = 0.05

    experiment = db.query(Experiment).filter_by(id=experiment_id).first()
    if not experiment or experiment.status != "running" or experiment.winner:
        return False  # already has a winner or not running

    variant_metrics = metrics_service.get_variant_metrics(db, experiment_id)
    if len(variant_metrics) < 2:
        return False

    variant_names = list(variant_metrics.keys())
    control_name, treatment_name = variant_names[0], variant_names[1]
    primary_metric = experiment.primary_metric or "latency_ms"
    metric_key, lower_is_better = METRIC_CONFIG.get(primary_metric, ("latency_ms", True))

    control_values = variant_metrics[control_name][metric_key]["values"]
    treatment_values = variant_metrics[treatment_name][metric_key]["values"]

    result = stats_service.compare_variants(
        control_name=control_name,
        control_values=control_values,
        treatment_name=treatment_name,
        treatment_values=treatment_values,
        metric=primary_metric,
        lower_is_better=lower_is_better,
    )

    p_value = result.get("p_value")
    winner = result.get("winner")

    if p_value is not None and p_value < WINNER_SIGNIFICANCE_LEVEL and winner:
        experiment.winner = winner
        db.commit()
        owner = experiment.owner or "unset"
        print(
            f"[notify] Experiment {experiment_id} '{experiment.name}': "
            f"winner declared → {winner} (p={p_value:.4f}). "
            f"Owner: {owner}. Promote via POST /experiments/{experiment_id}/promote-winner"
        )
        return True

    return False


def _cancel_experiment(db: Session, experiment_id: int) -> None:
    experiment = db.query(Experiment).filter_by(id=experiment_id).first()
    if experiment:
        experiment.status = "cancelled"
        db.commit()