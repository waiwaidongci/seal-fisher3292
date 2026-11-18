import json
import numpy as np
from sqlalchemy import select,func
from src.config.database import Base,engine,Session
from src.config.domain import DOMAIN
from src.models.user import User
from src.models.study import Study
from src.models.observation import Observation
from src.models import run,review,audit,staged
from src.utils.security import hash_password
from types import SimpleNamespace
from fastapi import HTTPException
from src.schemas.staged import StagedLookInput,StagedPlanInput
from src.services import staged_plans


def seed_staged_plan(db,study,name,looks,total_alpha,admitted):
    """Create a demo plan through the real service rules, once, then leave it alone."""
    if db.scalar(select(staged.StagedPlan).where(staged.StagedPlan.study_id==study.id,staged.StagedPlan.name==name)):
        return None
    user=SimpleNamespace(email='admin@demo.local')
    payload=StagedPlanInput(study_id=study.id,name=name,looks=[{'n':n,'alpha':a} for n,a in looks],total_alpha=total_alpha)
    try:
        plan=staged_plans.create(db,payload,user)
    except HTTPException:
        return None
    for number,values in admitted:
        a_success,a_failure,b_success,b_failure=values
        look=StagedLookInput(look=number,arm_a_success=a_success,arm_a_failure=a_failure,
            arm_b_success=b_success,arm_b_failure=b_failure,
            idempotency_key=f'seed-demo-{plan["id"]}-{number}',note='')
        result=staged_plans.admit(db,plan['id'],look,user)
        if result['status']!='active': break
    return plan

def seed():
    Base.metadata.create_all(engine)
    rng=np.random.default_rng(42)
    i=DOMAIN['index']
    with Session() as db:
        for role in ('admin','analyst','viewer'):
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)): db.add(User(email=email,role=role,password_hash=hash_password('Research2026!')))
        for k,name in enumerate(DOMAIN['names']):
            study=db.scalar(select(Study).where(Study.name==name))
            if not study:
                study=Study(name=name,source_filename=DOMAIN['slug']+f'-202611-{k+1}.csv',description='独立合成业务观测 / 固定种子42 / 仅用于方法与流程演示');db.add(study);db.flush()
            rows=[]
            if i==0:
                for n in range(120): rows.append({'moisture':round(float(np.clip(rng.normal(5.2+k*.7,.7),0,100)),3),'extract':round(float(np.clip(rng.normal(34+k*2,2.4),0,100)),3)})
            elif i==1:
                rows=[{'group':'A','success':87+k*2,'failure':13-k*2},{'group':'B','success':96-k,'failure':4+k}]
            elif i==2:
                for n in range(24):
                    b=float(rng.uniform(3,6));rows.append({'before':round(b,3),'after':round(max(.1,b+rng.normal(-.45+k*.3,.5)),3)})
            elif i==3:
                for n in range(90):
                    x=round(float(rng.uniform(4,28)),1);rows.append({'porosity':x,'absorption':round(max(0,x*(.18+k*.08)+rng.normal(0,1.5)),2)})
            else: rows=[{'p0':.08+k*.02,'p1':.1+k*.025,'cost_per_contact':.12+k*.04,'budget':3000+k*500}]
            for n,values in enumerate(rows):
                key=f'DEMO-{k+1}-{n+1:04}'
                if not db.scalar(select(Observation.id).where(Observation.study_id==study.id,Observation.source_key==key)):
                    db.add(Observation(study_id=study.id,source_key=key,values=values))
            if i==1 and k==0:
                seed_staged_plan(db,study,'砚田·薄膜分阶段三轮',[(4,'0.02'),(8,'0.02'),(12,'0.02')],'0.05',
                    [(1,(3,1,3,1)),(2,(4,0,1,3))])
            if i==1 and k==1:
                seed_staged_plan(db,study,'砚田·覆膜分阶段两轮',[(6,'0.03'),(12,'0.03')],'0.05',
                    [(1,(5,1,0,6))])
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in (User,Study,Observation,staged.StagedPlan,staged.StagedLook)}
        print(json.dumps(counts));return counts
if __name__=='__main__': seed()
