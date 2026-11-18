from src.services import staged_plans


def preview(db, data, user): return staged_plans.preview(db, data)
def create_plan(db, data, user): return staged_plans.create(db, data, user)
def list_plans(db, study_id=None): return staged_plans.list_plans(db, study_id)
def get_plan(db, id): return staged_plans.get(db, id)
def update_plan(db, id, data, user): return staged_plans.update_draft(db, id, data, user)
def admit_look(db, id, data, user): return staged_plans.admit(db, id, data, user)
