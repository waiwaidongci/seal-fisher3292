from pydantic import BaseModel, Field


class LookInput(BaseModel):
    n: int = Field(ge=1, le=12)
    alpha: str = Field(min_length=1, max_length=40)


class StagedPlanInput(BaseModel):
    study_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=120)
    looks: list[LookInput] = Field(min_length=1, max_length=3)
    total_alpha: str = Field(min_length=1, max_length=40)


class StagedLookInput(BaseModel):
    """Both arms' increments for the next look, admitted together or not at all."""

    look: int = Field(ge=1, le=3, description="本次提交对应的预登记查看序号，必须是下一阶段")
    arm_a_success: int = Field(ge=0, le=12)
    arm_a_failure: int = Field(ge=0, le=12)
    arm_b_success: int = Field(ge=0, le=12)
    arm_b_failure: int = Field(ge=0, le=12)
    idempotency_key: str = Field(min_length=8, max_length=120)
    note: str = Field(default='', max_length=2000)
