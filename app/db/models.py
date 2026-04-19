from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, JSON
from sqlalchemy.sql import func
from app.db.database import Base
from sqlalchemy.orm import relationship
from datetime import datetime


class Prompt(Base):
    __tablename__ = "prompts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)

    active_version_id = Column(Integer, ForeignKey("prompt_versions.id"), nullable=True)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id = Column(Integer, primary_key=True, index=True)
    prompt_id = Column(Integer, ForeignKey("prompts.id"), nullable=False)

    version = Column(Integer, nullable=False)

    system_prompt = Column(Text, nullable=False)
    variables = Column(JSON, nullable=True)

    temperature = Column(String, nullable=True)
    max_tokens = Column(Integer, nullable=True)
    few_shot_examples = Column(JSON, nullable=True)

    commit_message = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

class PromptAudit(Base):
    __tablename__ = "prompt_audit"

    id = Column(Integer, primary_key=True, index=True)
    prompt_id = Column(Integer, nullable=False)

    old_version_id = Column(Integer, nullable=True)
    new_version_id = Column(Integer, nullable=False)
    actor = Column(String, nullable=True)

    changed_at = Column(DateTime(timezone=True), server_default=func.now())




class Experiment(Base):
    __tablename__ = "experiments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)

    prompt_id = Column(Integer, ForeignKey("prompts.id"), nullable=False)

    primary_metric = Column(String, nullable=False)  # e.g., "latency", "quality"
    sample_size = Column(Integer, nullable=True)
    owner = Column(String, nullable=True)

    status = Column(String, default="draft")  # draft, running, completed, cancelled

    winner = Column(String, nullable=True)          # winning variant name once declared
    winner_promoted = Column(Integer, default=0)    # 1 if winner auto-promoted to active_version

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships (safe to add)
    variants = relationship("ExperimentVariant", back_populates="experiment")


class ExperimentVariant(Base):
    __tablename__ = "experiment_variants"

    id = Column(Integer, primary_key=True, index=True)

    experiment_id = Column(Integer, ForeignKey("experiments.id"), nullable=False)

    variant_name = Column(String, nullable=False)  # "A", "B", etc.

    prompt_version_id = Column(Integer, ForeignKey("prompt_versions.id"), nullable=False)

    traffic_percentage = Column(Integer, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    experiment = relationship("Experiment", back_populates="variants")

class ExperimentRun(Base):
    __tablename__ = "experiment_runs"

    id = Column(Integer, primary_key=True, index=True)

    experiment_id = Column(Integer, ForeignKey("experiments.id"), nullable=False)
    user_id = Column(String, nullable=False)

    variant = Column(String, nullable=False)

    # Metrics captured at serve time — needed for Phase 3 statistical analysis
    latency_ms = Column(Integer, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    is_error = Column(Integer, default=0)  # 0 = success, 1 = error
    response_text = Column(Text, nullable=True)
    quality_score = Column(Integer, nullable=True)  # LLM-as-judge score 1–5

    created_at = Column(DateTime(timezone=True), server_default=func.now())