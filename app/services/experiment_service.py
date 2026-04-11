from sqlalchemy.orm import Session
from app.db.models import Experiment, ExperimentVariant, PromptVersion
from app.services.hash_service import assign_variant
from app.db.models import ExperimentRun


def get_variant_for_user(db: Session, experiment_id: int, user_id: str):
    # Step 1: Get experiment
    experiment = db.query(Experiment).filter(Experiment.id == experiment_id).first()
    if not experiment:
        return None

    # Step 2: Get variants
    variants = db.query(ExperimentVariant).filter(
        ExperimentVariant.experiment_id == experiment_id
    ).all()

    if not variants:
        return None

    # Step 3: Prepare for hashing
    variant_list = [
        {"name": v.variant_name, "traffic": v.traffic_percentage}
        for v in variants
    ]

    # Step 4: Assign variant
    assigned_variant = assign_variant(user_id, variant_list)

    # Step 5: Find selected variant row
    selected_variant = next(
        (v for v in variants if v.variant_name == assigned_variant),
        None
    )

    if not selected_variant:
        return None

    # Step 6: Get prompt version
    prompt_version = db.query(PromptVersion).filter(
        PromptVersion.id == selected_variant.prompt_version_id
    ).first()

    # Log experiment run
    existing_run = db.query(ExperimentRun).filter(
        ExperimentRun.experiment_id == experiment_id,
        ExperimentRun.user_id == user_id
    ).first()

    if not existing_run:
        run = ExperimentRun(
            experiment_id=experiment_id,
            user_id=user_id,
            variant=assigned_variant
        )
        db.add(run)
        db.commit()

    return {
        "variant": assigned_variant,
        "prompt_version_id": selected_variant.prompt_version_id,
        "prompt": prompt_version.system_prompt if prompt_version else None
    }