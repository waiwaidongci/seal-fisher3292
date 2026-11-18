from sqlalchemy import Column, Integer, String, JSON, Text, ForeignKey, UniqueConstraint
from src.config.database import Base


class StagedPlan(Base):
    """Pre-registered group-sequential plan: cumulative n per arm and per-look thresholds."""

    __tablename__ = 'staged_plans'
    __table_args__ = (UniqueConstraint('study_id', 'name', name='uq_staged_plan_name'),)
    id = Column(Integer, primary_key=True)
    study_id = Column(Integer, ForeignKey('studies.id'), nullable=False)
    name = Column(String(120), nullable=False)
    looks = Column(JSON, nullable=False)
    total_alpha = Column(String(80), nullable=False)
    calibration = Column(JSON, nullable=False)
    checksum = Column(String(64), nullable=False)
    status = Column(String(20), default='draft', nullable=False)
    next_look = Column(Integer, default=1, nullable=False)
    stop_look = Column(Integer, nullable=True)
    stop_reason = Column(Text, default='', nullable=False)
    stop_p = Column(String(80), default='', nullable=False)
    locked_at = Column(String(40), default='', nullable=False)
    created_by = Column(String(120), nullable=False)
    created_at = Column(String(40), nullable=False)


class StagedLook(Base):
    """One admitted stage increment for both arms, with its exact Fisher verdict."""

    __tablename__ = 'staged_looks'
    __table_args__ = (
        UniqueConstraint('plan_id', 'look', name='uq_staged_look_number'),
        UniqueConstraint('plan_id', 'idempotency_key', name='uq_staged_look_key'),
    )
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey('staged_plans.id'), nullable=False)
    look = Column(Integer, nullable=False)
    n_per_arm = Column(Integer, nullable=False)
    increments = Column(JSON, nullable=False)
    cumulative = Column(JSON, nullable=False)
    fisher_p = Column(String(80), nullable=False)
    fisher_p_value = Column(String(40), nullable=False)
    threshold = Column(String(80), nullable=False)
    decision = Column(String(20), nullable=False)
    reason = Column(Text, nullable=False)
    idempotency_key = Column(String(120), nullable=False)
    actor = Column(String(120), nullable=False)
    created_at = Column(String(40), nullable=False)
