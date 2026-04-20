import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from app.db.database import get_db
from app.db import models
from app.schemas import prompt as schemas
from app.services import llm_service

router = APIRouter()


@router.post("/prompts")
def create_prompt(data: schemas.PromptCreate, db: Session = Depends(get_db)):
    prompt = models.Prompt(name=data.name)
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    return prompt


@router.post("/prompts/{prompt_id}/versions")
def create_prompt_version(prompt_id: int, data: schemas.PromptVersionCreate, db: Session = Depends(get_db)):
    prompt = db.query(models.Prompt).filter_by(id=prompt_id).first()
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")

    latest = (
        db.query(models.PromptVersion)
        .filter(models.PromptVersion.prompt_id == prompt_id)
        .order_by(models.PromptVersion.version.desc())
        .first()
    )

    new_version = 1 if not latest else latest.version + 1

    version = models.PromptVersion(
        prompt_id=prompt_id,
        version=new_version,
        system_prompt=data.system_prompt,
        variables=data.variables,
        temperature=data.temperature,
        max_tokens=data.max_tokens,
        few_shot_examples=data.few_shot_examples,
        commit_message=data.commit_message,
    )

    db.add(version)
    db.commit()
    db.refresh(version)

    if prompt.active_version_id is None:
        prompt.active_version_id = version.id
        db.commit()
        db.refresh(version)  # re-load after second commit or SQLAlchemy returns {}

    return version


@router.get("/prompts/{prompt_id}/versions")
def get_versions(prompt_id: int, db: Session = Depends(get_db)):
    return db.query(models.PromptVersion).filter_by(prompt_id=prompt_id).all()


@router.get("/prompts/{prompt_id}/versions/{version_number}")
def get_version_by_number(prompt_id: int, version_number: int, db: Session = Depends(get_db)):
    """Fetch a specific version by its sequential version number (not DB id)."""
    version = (
        db.query(models.PromptVersion)
        .filter_by(prompt_id=prompt_id, version=version_number)
        .first()
    )
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    return version


@router.put("/prompts/{prompt_id}/activate/{version_id}")
def set_active_version(prompt_id: int, version_id: int, actor: str = None, db: Session = Depends(get_db)):
    prompt = db.query(models.Prompt).filter_by(id=prompt_id).first()
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")

    version = db.query(models.PromptVersion).filter_by(id=version_id, prompt_id=prompt_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found for this prompt")

    old_version = prompt.active_version_id
    prompt.active_version_id = version_id
    db.commit()

    audit = models.PromptAudit(
        prompt_id=prompt_id,
        old_version_id=old_version,
        new_version_id=version_id,
        actor=actor,
    )
    db.add(audit)
    db.commit()

    return {"message": "Active version updated", "active_version_id": version_id}


@router.get("/prompts/{prompt_id}/diff/{v1_id}/{v2_id}")
def diff_versions(prompt_id: int, v1_id: int, v2_id: int, db: Session = Depends(get_db)):
    v1 = db.query(models.PromptVersion).filter_by(id=v1_id, prompt_id=prompt_id).first()
    v2 = db.query(models.PromptVersion).filter_by(id=v2_id, prompt_id=prompt_id).first()

    if not v1 or not v2:
        raise HTTPException(status_code=404, detail="One or both versions not found")

    diff = {}

    if v1.system_prompt != v2.system_prompt:
        diff["system_prompt"] = {"from": v1.system_prompt, "to": v2.system_prompt}
    if v1.temperature != v2.temperature:
        diff["temperature"] = {"from": v1.temperature, "to": v2.temperature}
    if v1.max_tokens != v2.max_tokens:
        diff["max_tokens"] = {"from": v1.max_tokens, "to": v2.max_tokens}
    if v1.few_shot_examples != v2.few_shot_examples:
        diff["few_shot_examples"] = {"from": v1.few_shot_examples, "to": v2.few_shot_examples}
    if v1.commit_message != v2.commit_message:
        diff["commit_message"] = {"from": v1.commit_message, "to": v2.commit_message}

    return {"diff": diff}


class CompareRequest(BaseModel):
    version_a_id: int
    version_b_id: int
    variables: Optional[dict] = None


@router.post("/prompts/{prompt_id}/compare")
def compare_versions(prompt_id: int, data: CompareRequest, db: Session = Depends(get_db)):
    """
    Run the same input through two prompt versions and return outputs side by side.
    Use this before starting an A/B experiment to sanity-check both variants.
    """
    v_a = db.query(models.PromptVersion).filter_by(id=data.version_a_id, prompt_id=prompt_id).first()
    v_b = db.query(models.PromptVersion).filter_by(id=data.version_b_id, prompt_id=prompt_id).first()
    if not v_a or not v_b:
        raise HTTPException(status_code=404, detail="One or both versions not found for this prompt")

    for label, version in [("version_a", v_a), ("version_b", v_b)]:
        required = set(re.findall(r"\{\{(\w+)\}\}", version.system_prompt))
        provided = set(data.variables.keys()) if data.variables else set()
        missing = required - provided
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"{label} missing template variables: {sorted(missing)}",
            )

    result_a = llm_service.complete(v_a.system_prompt, data.variables or {})
    result_b = llm_service.complete(v_b.system_prompt, data.variables or {})

    return {
        "prompt_id": prompt_id,
        "version_a": {
            "version_id": v_a.id,
            "version_number": v_a.version,
            "response": result_a.response_text,
            "latency_ms": result_a.latency_ms,
            "input_tokens": result_a.input_tokens,
            "output_tokens": result_a.output_tokens,
            "is_error": result_a.is_error,
        },
        "version_b": {
            "version_id": v_b.id,
            "version_number": v_b.version,
            "response": result_b.response_text,
            "latency_ms": result_b.latency_ms,
            "input_tokens": result_b.input_tokens,
            "output_tokens": result_b.output_tokens,
            "is_error": result_b.is_error,
        },
    }
