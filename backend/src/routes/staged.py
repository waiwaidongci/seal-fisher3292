from fastapi import APIRouter, Depends

from src.config.database import get_db
from src.controllers import staged as controller
from src.middlewares.auth import current_user, writer
from src.schemas.staged import StagedLookInput, StagedPlanInput
from src.services import staged_plans

router = APIRouter()


@router.post('/staged-plans/preview')
def preview(data: StagedPlanInput, db=Depends(get_db), user=Depends(writer)):
    return controller.preview(db, data, user)


@router.get('/staged-plans')
def list_plans(study_id: int | None = None, db=Depends(get_db), user=Depends(current_user)):
    return staged_plans.list_plans(db, study_id)


@router.post('/staged-plans')
def create_plan(data: StagedPlanInput, db=Depends(get_db), user=Depends(writer)):
    return controller.create_plan(db, data, user)


@router.get('/staged-plans/{id}')
def get_plan(id: int, db=Depends(get_db), user=Depends(current_user)):
    return controller.get_plan(db, id)


@router.put('/staged-plans/{id}')
def update_plan(id: int, data: StagedPlanInput, db=Depends(get_db), user=Depends(writer)):
    return controller.update_plan(db, id, data, user)


@router.post('/staged-plans/{id}/looks')
def admit_look(id: int, data: StagedLookInput, db=Depends(get_db), user=Depends(writer)):
    return controller.admit_look(db, id, data, user)
