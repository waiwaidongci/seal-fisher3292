import os,time,json,logging
from pathlib import Path
from redis import Redis
from sqlalchemy import select
from src.config.database import Session
from src.models.run import Run
from src.models import user,study,observation,review,audit,staged
from src.services.algorithm import calculate
from src.utils.serializers import checksum
ARTIFACTS=Path(os.environ.get('ARTIFACTS_DIR','/app/artifacts'))
redis=Redis.from_url(os.environ['REDIS_URL'])
def recover(db,push):
    """Re-queue interrupted exact runs only.

    Staged plans are deliberately untouched: each look, its Fisher verdict and
    the plan's stop/completion state are committed in one transaction, so a
    restart keeps every stop conclusion and never re-opens a stopped trial.
    """
    pending=list(db.scalars(select(Run).where(Run.status.in_(['queued','running']))))
    for run in pending: push(run.id)
    return [run.id for run in pending]
def main():
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    # Recover jobs interrupted after dequeue. A project runs exactly one worker.
    with Session() as db: recover(db,lambda run_id: redis.rpush('analysis-queue',run_id))
    while True:
        Path('/tmp/worker-heartbeat').touch()
        job=redis.blpop('analysis-queue',timeout=2)
        if not job: continue
        id=int(job[1])
        with Session() as db:
            run=db.get(Run,id)
            if not run or run.status in ('completed','failed'): continue
            run.status='running';db.commit()
            try:
                if checksum(run.snapshot)!=run.checksum: raise ValueError('输入快照校验失败')
                result=calculate(run.snapshot,run.parameters)
                result['input_sha256']=run.checksum
                run.result=result;run.status='completed'
                (ARTIFACTS/f'run-{id}.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))
            except Exception as e:
                logging.exception('analysis failed');run.status='failed';run.error=str(e)
            db.commit()
if __name__=='__main__': main()
