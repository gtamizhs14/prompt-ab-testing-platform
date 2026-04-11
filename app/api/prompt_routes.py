from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.db import models
from app.schemas import prompt as schemas

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/prompts")
def create_prompt(data: schemas.PromptCreate, db: Session = Depends(get_db)):
    prompt = models.Prompt(name=data.name)
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    return prompt


@router.post("/prompts/{prompt_id}/versions")
def create_prompt_version(prompt_id: int, data: schemas.PromptVersionCreate, db: Session = Depends(get_db)):

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
        commit_message=data.commit_message,
    )

    db.add(version)
    db.commit()
    db.refresh(version)

    # set active version if first version
    prompt = db.query(models.Prompt).filter_by(id=prompt_id).first()

    if prompt.active_version_id is None:
        prompt.active_version_id = version.id
        db.commit()

    return version


@router.get("/prompts/{prompt_id}/versions")
def get_versions(prompt_id: int, db: Session = Depends(get_db)):
    return db.query(models.PromptVersion).filter_by(prompt_id=prompt_id).all()

@router.put("/prompts/{prompt_id}/activate/{version_id}")
def set_active_version(prompt_id: int, version_id: int, db: Session = Depends(get_db)):

    prompt = db.query(models.Prompt).filter_by(id=prompt_id).first()

    if not prompt:
        return {"error": "Prompt not found"}

    version = db.query(models.PromptVersion).filter_by(id=version_id, prompt_id=prompt_id).first()

    if not version:
        return {"error": "Version not found for this prompt"}

    old_version = prompt.active_version_id

    prompt.active_version_id = version_id
    db.commit()

    # 🔥 ADD AUDIT LOG
    audit = models.PromptAudit(
        prompt_id=prompt_id,
        old_version_id=old_version,
        new_version_id=version_id
    )

    db.add(audit)
    db.commit()

    return {"message": "Active version updated", "active_version_id": version_id}

@router.get("/prompts/{prompt_id}/diff/{v1_id}/{v2_id}")
def diff_versions(prompt_id: int, v1_id: int, v2_id: int, db: Session = Depends(get_db)):

    v1 = db.query(models.PromptVersion).filter_by(id=v1_id, prompt_id=prompt_id).first()
    v2 = db.query(models.PromptVersion).filter_by(id=v2_id, prompt_id=prompt_id).first()

    if not v1 or not v2:
        return {"error": "One or both versions not found"}

    diff = {}

    if v1.system_prompt != v2.system_prompt:
        diff["system_prompt"] = {"from": v1.system_prompt, "to": v2.system_prompt}

    if v1.temperature != v2.temperature:
        diff["temperature"] = {"from": v1.temperature, "to": v2.temperature}

    if v1.max_tokens != v2.max_tokens:
        diff["max_tokens"] = {"from": v1.max_tokens, "to": v2.max_tokens}

    if v1.commit_message != v2.commit_message:
        diff["commit_message"] = {"from": v1.commit_message, "to": v2.commit_message}

    return {"diff": diff}