"""Pre-registered group-sequential seal-trial plans.

Rules implemented here:

* a plan freezes cumulative sample size per arm (<= 12) and the per-look
  threshold for at most three looks, and is locked once the first look is
  admitted;
* before activation the exact maximum type-I error over a common pass rate
  ``p in (0, 1)`` is computed; a plan above the total limit is refused;
* each look is judged with the two-sided Fisher exact test on the cumulative
  table; the first crossing stops the trial permanently, otherwise the trial
  ends after the final look;
* only the next look's increments are accepted, both arms are booked in one
  transaction, and a repeated/concurrent submission never advances twice.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.models.staged import StagedLook, StagedPlan
from src.services import studies
from src.services import staged
from src.services.audits import log
from src.utils.serializers import checksum, now, record

STATUS_LABELS = {
    "active": "进行中",
    "stopped": "已停试",
    "completed": "已到末次结束",
}


def _calibration(looks, total_alpha):
    try:
        return staged.validation_report(looks, total_alpha)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def _serialize(plan, looks):
    payload = record(plan)
    payload["calibration"] = plan.calibration
    payload["looks"] = [record(item) for item in looks]
    payload["status_label"] = STATUS_LABELS.get(plan.status, plan.status)
    payload["looks_planned"] = len(plan.looks)
    payload["next_n"] = plan.looks[plan.next_look - 1]["n"] if plan.next_look <= len(plan.looks) else None
    payload["next_delta"] = plan.looks[plan.next_look - 1]["delta"] if plan.next_look <= len(plan.looks) else None
    payload["locked"] = plan.next_look > 1
    payload["can_admit"] = plan.status == "active" and plan.next_look <= len(plan.looks)
    payload["can_edit"] = plan.status == "active" and plan.next_look == 1
    payload["max_type1"] = plan.calibration.get("max_type1")
    payload["total_alpha"] = plan.calibration.get("total_alpha")
    payload["gate"] = plan.calibration.get("decision")
    return payload


def _looks(db, plan_id):
    return list(db.scalars(select(StagedLook).where(StagedLook.plan_id == plan_id).order_by(StagedLook.look)))


def _find(db, plan_id):
    plan = db.get(StagedPlan, plan_id)
    if not plan:
        raise HTTPException(404, "试封计划不存在")
    return plan


def _plan_key(looks, total_alpha):
    return checksum({"looks": looks, "total_alpha": total_alpha})


def create(db, data, user):
    studies.require(db, data.study_id)
    looks = [item.model_dump() for item in data.looks]
    report = _calibration(looks, data.total_alpha)
    normalized = report["looks"]
    if report["exceeds_limit"]:
        raise HTTPException(
            422,
            "计划校验未通过：共同合格率在0到1间的最大误拒概率 "
            f"{report['max_type1']:.6f} 超过总限额 {report['total_alpha']}，拒绝启用",
        )
    if db.scalar(select(StagedPlan).where(StagedPlan.study_id == data.study_id, StagedPlan.name == data.name)):
        raise HTTPException(409, "同一方案下试封计划名称已存在")
    plan = StagedPlan(
        study_id=data.study_id,
        name=data.name,
        looks=normalized,
        total_alpha=report["total_alpha"],
        calibration=report,
        checksum=_plan_key(normalized, report["total_alpha"]),
        status="active",
        next_look=1,
        stop_reason="",
        stop_p="",
        locked_at="",
        created_by=user.email,
        created_at=now(),
    )
    db.add(plan)
    db.flush()
    log(db, user, "staged_plan.create", f"{plan.id}: {data.name}")
    db.commit()
    return _serialize(plan, [])


def preview(db, data):
    studies.require(db, data.study_id)
    return _calibration([item.model_dump() for item in data.looks], data.total_alpha)


def list_plans(db, study_id=None):
    query = select(StagedPlan).order_by(StagedPlan.id.desc())
    if study_id:
        query = query.where(StagedPlan.study_id == study_id)
    out = []
    for plan in db.scalars(query):
        out.append(_serialize(plan, _looks(db, plan.id)))
    return out


def get(db, plan_id):
    plan = _find(db, plan_id)
    return _serialize(plan, _looks(db, plan.id))


def update_draft(db, plan_id, data, user):
    """Edit the pre-registration only before the first look is admitted.

    The row is locked so a concurrent first admission cannot slip between the
    lock check and the write, which would otherwise rewrite a frozen schedule.
    """
    plan = _locked_plan(db, plan_id)
    if plan.next_look > 1 or plan.status != "active":
        db.rollback()
        raise HTTPException(409, "首批已入账，计划已锁定，不能再修改")
    looks = [item.model_dump() for item in data.looks]
    report = _calibration(looks, data.total_alpha)
    if report["exceeds_limit"]:
        raise HTTPException(
            422,
            "计划校验未通过：共同合格率在0到1间的最大误拒概率 "
            f"{report['max_type1']:.6f} 超过总限额 {report['total_alpha']}，拒绝启用",
        )
    duplicate = db.scalar(
        select(StagedPlan).where(
            StagedPlan.study_id == data.study_id,
            StagedPlan.name == data.name,
            StagedPlan.id != plan.id,
        )
    )
    if duplicate:
        raise HTTPException(409, "同一方案下试封计划名称已存在")
    normalized = report["looks"]
    plan.study_id = data.study_id
    plan.name = data.name
    plan.looks = normalized
    plan.total_alpha = report["total_alpha"]
    plan.calibration = report
    plan.checksum = _plan_key(normalized, report["total_alpha"])
    log(db, user, "staged_plan.update", str(plan.id))
    db.commit()
    return _serialize(plan, _looks(db, plan.id))


def _cumulative(looks):
    total = {"A": {"success": 0, "failure": 0}, "B": {"success": 0, "failure": 0}}
    for item in looks:
        increments = item["increments"] if isinstance(item, dict) else item.increments
        for arm in ("A", "B"):
            total[arm]["success"] += increments[arm]["success"]
            total[arm]["failure"] += increments[arm]["failure"]
    return total


def _replay(db, plan):
    return _serialize(plan, _looks(db, plan.id))


def _locked_plan(db, plan_id):
    plan = db.scalar(select(StagedPlan).where(StagedPlan.id == plan_id).with_for_update())
    if not plan:
        raise HTTPException(404, "试封计划不存在")
    return plan


def admit(db, plan_id, data, user):
    """Admit the next look's increments for both arms in one transaction.

    The plan row is locked before any consistent read, so under MySQL's
    repeatable-read isolation a request that waited for the lock still sees the
    previous request's committed look.  That keeps cumulative counts correct and
    prevents two requests from advancing the same stage.
    """
    plan = _locked_plan(db, plan_id)
    existing = db.scalar(
        select(StagedLook).where(StagedLook.plan_id == plan_id, StagedLook.idempotency_key == data.idempotency_key)
    )
    if existing:
        return _replay(db, plan)

    if plan.status != "active":
        db.rollback()
        detail = plan.stop_reason or "计划已结束，不能继续入账"
        raise HTTPException(409, detail)
    if plan.next_look > len(plan.looks):
        db.rollback()
        raise HTTPException(409, "已到末次查看，计划结束")

    look_number = plan.next_look
    if data.look != look_number:
        db.rollback()
        raise HTTPException(
            409,
            f"本次提交声明第{data.look}次查看，但当前只接收第{look_number}次增量；"
            "并发或重试不得推进阶段，请刷新后重试",
        )
    scheduled = plan.looks[look_number - 1]
    delta = scheduled["delta"]
    increments = {
        "A": {"success": data.arm_a_success, "failure": data.arm_a_failure},
        "B": {"success": data.arm_b_success, "failure": data.arm_b_failure},
    }
    for arm in ("A", "B"):
        if increments[arm]["success"] + increments[arm]["failure"] != delta:
            raise HTTPException(
                422,
                f"仅接收下一阶段增量：第{look_number}次查看{arm}组应新增{delta}件"
                f"（合格+失败），实际为{increments[arm]['success'] + increments[arm]['failure']}件",
            )

    prior = _looks(db, plan_id)
    cumulative = _cumulative(prior)
    for arm in ("A", "B"):
        cumulative[arm]["success"] += increments[arm]["success"]
        cumulative[arm]["failure"] += increments[arm]["failure"]
        if cumulative[arm]["success"] + cumulative[arm]["failure"] != scheduled["n"]:
            raise HTTPException(422, "累计样本量与预登记不一致")
        if any(value < 0 for value in cumulative[arm].values()):
            raise HTTPException(422, "计数不得为负")

    table = cumulative
    p_exact = staged.fisher_two_sided_exact(
        table["A"]["success"],
        table["A"]["failure"],
        table["B"]["success"],
        table["B"]["failure"],
    )
    threshold = staged.as_fraction(scheduled["alpha"])
    crossed = p_exact <= threshold

    if crossed:
        reason = (
            f"第{look_number}次查看双侧Fisher p={staged.exact_text(p_exact)}"
            f" ≤ 阈值 {scheduled['alpha']}，首次越界，永久停试"
        )
        decision = "stop"
    elif look_number == len(plan.looks):
        reason = (
            f"第{look_number}次（末次）查看双侧Fisher p={staged.exact_text(p_exact)}"
            f" > 阈值 {scheduled['alpha']}，未越界，按计划结束"
        )
        decision = "continue"
    else:
        reason = (
            f"第{look_number}次查看双侧Fisher p={staged.exact_text(p_exact)}"
            f" > 阈值 {scheduled['alpha']}，未越界，进入下一阶段"
        )
        decision = "continue"

    row = StagedLook(
        plan_id=plan_id,
        look=look_number,
        n_per_arm=scheduled["n"],
        increments=increments,
        cumulative=cumulative,
        fisher_p=staged.exact_text(p_exact),
        fisher_p_value=format(float(p_exact), ".17g"),
        threshold=scheduled["alpha"],
        decision=decision,
        reason=reason,
        idempotency_key=data.idempotency_key,
        actor=user.email,
        created_at=now(),
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        replay = db.scalar(
            select(StagedLook).where(StagedLook.plan_id == plan_id, StagedLook.idempotency_key == data.idempotency_key)
        )
        if replay:
            return _replay(db, _find(db, plan_id))
        raise HTTPException(409, "另一笔入账已推进到下一阶段，本次增量未入账，请刷新后按新阶段重新提交")

    plan.next_look = look_number + 1
    if crossed:
        plan.status = "stopped"
        plan.stop_look = look_number
        plan.stop_reason = reason
        plan.stop_p = staged.exact_text(p_exact)
    elif look_number == len(plan.looks):
        plan.status = "completed"
        plan.stop_look = None
        plan.stop_reason = reason
        plan.stop_p = staged.exact_text(p_exact)
    if look_number == 1:
        plan.locked_at = now()
    log(db, user, "staged_plan.admit", f"{plan_id}/{look_number}: {reason}")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        replay = db.scalar(
            select(StagedLook).where(StagedLook.plan_id == plan_id, StagedLook.idempotency_key == data.idempotency_key)
        )
        if replay:
            return _replay(db, _find(db, plan_id))
        raise HTTPException(409, "并发入账冲突，本次增量未生效，请刷新后重新提交")

    return _replay(db, _find(db, plan_id))
