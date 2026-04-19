import re
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.db.database import get_db, SessionLocal
from app.db.models import Experiment, ExperimentVariant, ExperimentRun
from app.services.experiment_service import (
    resolve_prompt_version,
    log_run,
    check_and_apply_auto_stop,
    check_performance_auto_stop,
    declare_winner_if_significant,
)
from app.services import llm_service
from app.services import metrics_service
from app.services import stats_service

router = APIRouter(prefix="/experiments", tags=["Experiments"])

# Maps primary_metric name → (values key in metrics dict, lower_is_better)
METRIC_CONFIG = {
    "latency_ms": ("latency_ms", True),
    "quality_score": ("quality_score", False),
}


# ── Request schemas ───────────────────────────────────────────────────────────

class ExperimentCreate(BaseModel):
    name: str
    prompt_id: int
    primary_metric: str = "latency_ms"  # "latency_ms" | "quality_score"
    sample_size: Optional[int] = None
    owner: Optional[str] = None


class VariantCreate(BaseModel):
    variant_name: str
    prompt_version_id: int
    traffic_percentage: int


class CompletionRequest(BaseModel):
    prompt_id: int
    user_id: str
    variables: Optional[dict] = None


# ── Background helpers ────────────────────────────────────────────────────────

def _async_score_run(run_id: int, prompt: str, response: str) -> None:
    """Score response asynchronously; opens its own DB session."""
    from app.services import judge_service
    score = judge_service.score_response(prompt, response)
    if score is None:
        return
    db = SessionLocal()
    try:
        run = db.query(ExperimentRun).filter_by(id=run_id).first()
        if run:
            run.quality_score = score
            db.commit()
    finally:
        db.close()


def _async_perf_auto_stop(experiment_id: int) -> None:
    """Performance-based auto-stop and winner declaration; own DB session."""
    db = SessionLocal()
    try:
        check_performance_auto_stop(db, experiment_id)
        declare_winner_if_significant(db, experiment_id)
    finally:
        db.close()


# ── Experiment CRUD ───────────────────────────────────────────────────────────

@router.post("")
def create_experiment(data: ExperimentCreate, db: Session = Depends(get_db)):
    """Create a new experiment in draft state."""
    if data.primary_metric not in METRIC_CONFIG:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid primary_metric. Choose from: {sorted(METRIC_CONFIG)}",
        )
    experiment = Experiment(
        name=data.name,
        prompt_id=data.prompt_id,
        primary_metric=data.primary_metric,
        sample_size=data.sample_size,
        owner=data.owner,
        status="draft",
    )
    db.add(experiment)
    db.commit()
    db.refresh(experiment)
    return experiment


@router.post("/{experiment_id}/variants")
def add_variant(experiment_id: int, data: VariantCreate, db: Session = Depends(get_db)):
    """Add a variant to a draft experiment."""
    experiment = db.query(Experiment).filter_by(id=experiment_id).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    if experiment.status != "draft":
        raise HTTPException(status_code=400, detail="Variants can only be added to draft experiments")
    if not (0 < data.traffic_percentage <= 100):
        raise HTTPException(status_code=400, detail="traffic_percentage must be between 1 and 100")

    variant = ExperimentVariant(
        experiment_id=experiment_id,
        variant_name=data.variant_name,
        prompt_version_id=data.prompt_version_id,
        traffic_percentage=data.traffic_percentage,
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant


@router.put("/{experiment_id}/start")
def start_experiment(experiment_id: int, db: Session = Depends(get_db)):
    """Transition experiment from draft → running. Validates traffic sums to 100."""
    experiment = db.query(Experiment).filter_by(id=experiment_id).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    if experiment.status != "draft":
        raise HTTPException(status_code=400, detail=f"Experiment is already '{experiment.status}'")

    variants = db.query(ExperimentVariant).filter_by(experiment_id=experiment_id).all()
    if len(variants) < 2:
        raise HTTPException(status_code=400, detail="Experiment needs at least 2 variants before starting")

    total_traffic = sum(v.traffic_percentage for v in variants)
    if total_traffic != 100:
        raise HTTPException(
            status_code=400,
            detail=f"Variant traffic must sum to 100 (currently {total_traffic})",
        )

    experiment.status = "running"
    db.commit()
    return {"message": "Experiment started", "experiment_id": experiment_id, "status": "running"}


@router.put("/{experiment_id}/stop")
def stop_experiment(experiment_id: int, db: Session = Depends(get_db)):
    """Manually stop a running experiment → completed."""
    experiment = db.query(Experiment).filter_by(id=experiment_id).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    if experiment.status != "running":
        raise HTTPException(status_code=400, detail=f"Experiment is not running (status: '{experiment.status}')")

    experiment.status = "completed"
    db.commit()
    return {"message": "Experiment stopped", "experiment_id": experiment_id, "status": "completed"}


@router.get("")
def list_experiments(db: Session = Depends(get_db)):
    """List all experiments with their variant count."""
    experiments = db.query(Experiment).order_by(Experiment.created_at.desc()).all()
    result = []
    for exp in experiments:
        variants = db.query(ExperimentVariant).filter_by(experiment_id=exp.id).all()
        result.append({
            "id": exp.id,
            "name": exp.name,
            "prompt_id": exp.prompt_id,
            "primary_metric": exp.primary_metric,
            "sample_size": exp.sample_size,
            "owner": exp.owner,
            "status": exp.status,
            "winner": exp.winner,
            "winner_promoted": bool(exp.winner_promoted),
            "variant_count": len(variants),
            "created_at": exp.created_at.isoformat() if exp.created_at else None,
        })
    return result


@router.get("/{experiment_id}")
def get_experiment(experiment_id: int, db: Session = Depends(get_db)):
    """Get experiment details with all variants."""
    experiment = db.query(Experiment).filter_by(id=experiment_id).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    variants = db.query(ExperimentVariant).filter_by(experiment_id=experiment_id).all()
    return {
        "id": experiment.id,
        "name": experiment.name,
        "prompt_id": experiment.prompt_id,
        "primary_metric": experiment.primary_metric,
        "sample_size": experiment.sample_size,
        "owner": experiment.owner,
        "status": experiment.status,
        "winner": experiment.winner,
        "winner_promoted": bool(experiment.winner_promoted),
        "created_at": experiment.created_at.isoformat() if experiment.created_at else None,
        "variants": [
            {
                "id": v.id,
                "variant_name": v.variant_name,
                "prompt_version_id": v.prompt_version_id,
                "traffic_percentage": v.traffic_percentage,
            }
            for v in variants
        ],
    }


@router.post("/{experiment_id}/promote-winner")
def promote_winner(experiment_id: int, db: Session = Depends(get_db)):
    """
    Promote the winning variant's prompt version to the prompt's active version.
    Can be called manually from the dashboard after a winner is declared.
    """
    from app.db.models import Prompt, PromptAudit
    experiment = db.query(Experiment).filter_by(id=experiment_id).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    if not experiment.winner:
        raise HTTPException(status_code=400, detail="No winner declared for this experiment yet")
    if experiment.winner_promoted:
        raise HTTPException(status_code=400, detail="Winner already promoted")

    winning_variant = (
        db.query(ExperimentVariant)
        .filter_by(experiment_id=experiment_id, variant_name=experiment.winner)
        .first()
    )
    if not winning_variant:
        raise HTTPException(status_code=404, detail="Winning variant not found")

    prompt = db.query(Prompt).filter_by(id=experiment.prompt_id).first()
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")

    old_version_id = prompt.active_version_id
    prompt.active_version_id = winning_variant.prompt_version_id
    experiment.winner_promoted = 1

    audit = PromptAudit(
        prompt_id=experiment.prompt_id,
        old_version_id=old_version_id,
        new_version_id=winning_variant.prompt_version_id,
        actor=f"experiment_{experiment_id}_winner",
    )
    db.add(audit)
    db.commit()

    return {
        "message": "Winner promoted to active version",
        "prompt_id": experiment.prompt_id,
        "new_active_version_id": winning_variant.prompt_version_id,
        "winning_variant": experiment.winner,
    }


# ── Serving / Results ─────────────────────────────────────────────────────────

@router.post("/v1/completions")
def serve_completion(
    request: CompletionRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Core serving endpoint. Resolves which prompt version to use for this user
    (active version or experiment variant), calls the LLM, logs the result,
    and triggers auto-stop if a variant's error rate is too high.

    Quality scoring runs asynchronously after the response is returned so it
    doesn't add latency to the caller.
    """
    version, experiment_id, variant = resolve_prompt_version(
        db, request.prompt_id, request.user_id
    )

    if not version:
        raise HTTPException(
            status_code=404,
            detail="No active prompt version or running experiment found for this prompt.",
        )

    required_vars = set(re.findall(r"\{\{(\w+)\}\}", version.system_prompt))
    provided_vars = set(request.variables.keys()) if request.variables else set()
    missing = required_vars - provided_vars
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing template variables: {sorted(missing)}",
        )

    result = llm_service.complete(version.system_prompt, request.variables or {})

    if experiment_id and variant:
        run = log_run(
            db=db,
            experiment_id=experiment_id,
            user_id=request.user_id,
            variant=variant,
            latency_ms=result.latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            is_error=result.is_error,
            response_text=result.response_text,
        )

        if not result.is_error:
            background_tasks.add_task(
                _async_score_run, run.id, version.system_prompt, result.response_text
            )
            background_tasks.add_task(_async_perf_auto_stop, experiment_id)

        if result.is_error:
            auto_stopped = check_and_apply_auto_stop(db, experiment_id, variant)
            if auto_stopped:
                return JSONResponse(
                    content={
                        "response": result.response_text,
                        "variant": variant,
                        "experiment_id": experiment_id,
                        "warning": "Experiment auto-stopped due to high error rate.",
                    },
                    headers={"X-Experiment-Status": "cancelled"},
                )

    return {
        "response": result.response_text,
        "prompt_version_id": version.id,
        "variant": variant,
        "experiment_id": experiment_id,
        "latency_ms": result.latency_ms,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }


@router.get("/{experiment_id}/results")
def get_experiment_results(experiment_id: int, db: Session = Depends(get_db)):
    """
    Returns live metrics and statistical significance for a running experiment.

    Uses the experiment's primary_metric to run the significance test, so the
    comparison is always on the metric that actually matters for this experiment.
    Also returns confidence interval, MDE, and sample size progress.
    """
    experiment = db.query(Experiment).filter_by(id=experiment_id).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    variant_metrics = metrics_service.get_variant_metrics(db, experiment_id)

    if len(variant_metrics) < 2:
        return {
            "experiment_id": experiment_id,
            "status": experiment.status,
            "variant_metrics": variant_metrics,
            "significance": None,
            "verdict": "insufficient_data",
        }

    variant_names = list(variant_metrics.keys())
    control_name = variant_names[0]
    treatment_name = variant_names[1]

    primary_metric = experiment.primary_metric or "latency_ms"
    metric_key, lower_is_better = METRIC_CONFIG.get(primary_metric, ("latency_ms", True))

    control_values = variant_metrics[control_name][metric_key]["values"]
    treatment_values = variant_metrics[treatment_name][metric_key]["values"]

    significance = stats_service.compare_variants(
        control_name=control_name,
        control_values=control_values,
        treatment_name=treatment_name,
        treatment_values=treatment_values,
        metric=primary_metric,
        lower_is_better=lower_is_better,
    )

    # Strip raw values before returning — only needed internally for t-test
    for v in variant_metrics.values():
        v["latency_ms"].pop("values", None)
        v["quality_score"].pop("values", None)

    return {
        "experiment_id": experiment_id,
        "status": experiment.status,
        "winner": experiment.winner,
        "winner_promoted": bool(experiment.winner_promoted),
        "primary_metric": primary_metric,
        "variant_metrics": variant_metrics,
        "significance": significance,
        "verdict": significance["verdict"],
        "winner": significance.get("winner"),
        "confidence_interval": significance.get("confidence_interval"),
        "mde": significance.get("mde"),
        "sample_size_progress": significance.get("sample_size_progress"),
    }


@router.get("/{experiment_id}/timeseries")
def get_timeseries(
    experiment_id: int,
    metric: str = "latency_ms",
    db: Session = Depends(get_db),
):
    """
    Returns per-variant time-ordered metric values for trend detection.
    Useful for spotting performance drift or warm-up effects over time.

    Supported metrics: latency_ms, quality_score, input_tokens, output_tokens
    """
    experiment = db.query(Experiment).filter_by(id=experiment_id).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    allowed = {"latency_ms", "quality_score", "input_tokens", "output_tokens"}
    if metric not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid metric '{metric}'. Choose from: {sorted(allowed)}",
        )

    series = metrics_service.get_timeseries(db, experiment_id, metric)
    return {"experiment_id": experiment_id, "metric": metric, "series": series}


@router.get("/{experiment_id}/assign")
def assign_variant_endpoint(experiment_id: int, user_id: str, db: Session = Depends(get_db)):
    """Debug endpoint — shows which variant a user would be assigned to."""
    from app.db.models import ExperimentVariant
    from app.services.hash_service import assign_variant

    experiment = db.query(Experiment).filter_by(id=experiment_id).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    variants = db.query(ExperimentVariant).filter_by(experiment_id=experiment_id).all()
    if not variants:
        raise HTTPException(status_code=404, detail="No variants configured")

    variant_list = [{"name": v.variant_name, "traffic": v.traffic_percentage} for v in variants]
    assigned = assign_variant(user_id, variant_list)

    return {"experiment_id": experiment_id, "user_id": user_id, "variant": assigned}
