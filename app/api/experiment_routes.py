from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.services.experiment_service import get_variant_for_user

router = APIRouter(prefix="/experiments", tags=["Experiments"])


@router.get("/{experiment_id}/assign")
def assign_variant(experiment_id: int, user_id: str, db: Session = Depends(get_db)):
    result = get_variant_for_user(db, experiment_id, user_id)

    if not result:
        return {"error": "Experiment or variants not found"}

    return {
        "experiment_id": experiment_id,
        "user_id": user_id,
        "variant": result["variant"],
        "prompt_version_id": result["prompt_version_id"],
        "prompt": result["prompt"]
    }