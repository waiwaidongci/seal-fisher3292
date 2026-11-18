"""Checks for pre-registered group-sequential seal-trial plans.

Run with ``python3 tests/test_staged_api.py`` from ``backend/``.  HTTP tests use
an in-memory SQLite database with stubbed auth; concurrency tests use a
file-backed SQLite database with independent sessions.  No Docker, MySQL or
Redis needed.  Do not add ``-I``: it hides the user site-packages where the
backend requirements live.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', 'sqlite+pysqlite:///:memory:')
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/15')
os.environ.setdefault('JWT_SECRET', 'test-secret')

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.config.database import Base, get_db
from src.middlewares.auth import current_user, writer
from src.models import audit, observation, review, run, staged, study, user
from src.models.study import Study
from src.models.user import User
from src.routes import staged as staged_routes

engine = create_engine(
    'sqlite+pysqlite://',
    connect_args={'check_same_thread': False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
Base.metadata.create_all(engine)

ADMIN = User(id=1, email='admin@demo.local', password_hash='x', role='admin')


def override_db():
    with TestingSession() as db:
        yield db


def override_admin():
    return ADMIN


app = FastAPI()
app.include_router(staged_routes.router)
app.dependency_overrides[get_db] = override_db
app.dependency_overrides[current_user] = override_admin
app.dependency_overrides[writer] = override_admin
client = TestClient(app)

with TestingSession() as db:
    db.add(Study(id=1, name='测试方案', source_filename='test.csv', description=''))
    db.commit()


def plan_payload(name, looks, total_alpha='0.05', study_id=1):
    return {
        'study_id': study_id,
        'name': name,
        'looks': [{'n': n, 'alpha': a} for n, a in looks],
        'total_alpha': total_alpha,
    }


def create_plan(name, looks, total_alpha='0.05'):
    response = client.post('/staged-plans', json=plan_payload(name, looks, total_alpha))
    assert response.status_code == 200, response.text
    return response.json()


def admit(plan_id, a_success, a_failure, b_success, b_failure, key, look=1):
    return client.post(
        f'/staged-plans/{plan_id}/looks',
        json={
            'look': look,
            'arm_a_success': a_success,
            'arm_a_failure': a_failure,
            'arm_b_success': b_success,
            'arm_b_failure': b_failure,
            'idempotency_key': key,
            'note': '',
        },
    )


def test_preview_is_exact_and_rejects_grid_substitute():
    response = client.post('/staged-plans/preview', json=plan_payload('预览', [(4, '0.05'), (8, '0.05'), (12, '0.05')]))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['threshold_sum_used_for_decision'] is False
    assert body['method'] == 'exact_rational_polynomial_bernstein_sturm'
    assert body['max_type1_lower_value'] <= body['max_type1'] <= body['max_type1_upper_value']
    assert body['threshold_sum_value'] == 0.15
    assert body['max_type1_upper_value'] < 0.15
    assert body['within_limit'] is True


def test_exact_max_catches_peak_a_coarse_grid_misses():
    """A 20-point grid under-reports this peak; the exact gate still decides on the true max."""
    from fractions import Fraction

    from src.services import staged as engine_module

    looks = [{'n': 2, 'alpha': '0.0436'}, {'n': 9, 'alpha': '0.0389'}]
    polynomial = engine_module.type1_polynomial(looks)
    exact_lower, exact_upper, _, _, certified = engine_module.certified_max(polynomial)
    assert certified
    grid = max(engine_module._eval(polynomial, Fraction(i, 20)) for i in range(21))
    assert grid < exact_lower - Fraction(1, 10**7)
    # A limit between the grid maximum and the true maximum: a grid-based gate
    # would pass the plan, the exact gate must refuse it.
    limit = exact_lower - Fraction(1, 10**8)
    assert grid < limit < exact_lower
    report = engine_module.validation_report(looks, engine_module.exact_text(limit))
    assert report['exceeds_limit'] is True
    assert report['decision'] == 'fail'


def test_over_limit_schedule_is_refused():
    response = client.post(
        '/staged-plans',
        json=plan_payload('超额', [(4, '0.1'), (8, '0.1'), (12, '0.1')], total_alpha='0.05'),
    )
    assert response.status_code == 422, response.text
    assert '最大误拒概率' in response.json()['detail']


def test_invalid_schedules_are_refused():
    cases = [
        ([(0, '0.05')], '0.05'),
        ([(13, '0.05')], '0.05'),
        ([(8, '0.05'), (8, '0.05')], '0.05'),
        ([(8, '0.05'), (4, '0.04')], '0.05'),
        ([(4, '0.05')] * 4, '0.05'),
        ([(4, '0')], '0.05'),
        ([(4, '1')], '0.05'),
        ([(4, '0.05')], '0'),
    ]
    for looks, total in cases:
        response = client.post('/staged-plans', json=plan_payload('非法', looks, total))
        assert response.status_code == 422, (looks, total, response.text)


def test_worker_recovery_keeps_stop_conclusion():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from src.config.database import Base
    from src.models.run import Run
    from src.models.staged import StagedLook, StagedPlan
    from src.worker import recover

    isolated = create_engine('sqlite+pysqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(isolated)
    factory = sessionmaker(bind=isolated, expire_on_commit=False)
    with factory() as db:
        db.add(Run(study_id=1, name='中断运行', status='running', parameters={}, snapshot=[], checksum='x', created_at='t'))
        db.add(
            StagedPlan(
                study_id=1,
                name='中断计划',
                looks=[{'look': 1, 'n': 4, 'delta': 4, 'alpha': '0.03'}],
                total_alpha='0.05',
                calibration={},
                checksum='c',
                status='stopped',
                next_look=2,
                stop_look=1,
                stop_reason='首次越界，永久停试',
                stop_p='1/100',
                locked_at='t',
                created_by='admin@demo.local',
                created_at='t',
            )
        )
        db.add(
            StagedLook(
                plan_id=1, look=1, n_per_arm=4, increments={}, cumulative={},
                fisher_p='1/100', fisher_p_value='0.01', threshold='0.03',
                decision='stop', reason='首次越界，永久停试', idempotency_key='k',
                actor='admin@demo.local', created_at='t',
            )
        )
        db.commit()
        pushed = recover(db, push=lambda run_id: None)
        assert pushed == [1]
        db.expire_all()
        plan = db.get(StagedPlan, 1)
        assert plan.status == 'stopped'
        assert plan.next_look == 2
        assert plan.stop_reason == '首次越界，永久停试'
        assert db.query(StagedLook).count() == 1


def test_plan_locks_after_first_look():
    plan = create_plan('锁定', [(4, '0.03'), (8, '0.03'), (12, '0.03')])
    assert plan['can_admit'] is True and plan['locked'] is False
    response = admit(plan['id'], 2, 2, 2, 2, 'lock-key-0001')
    assert response.status_code == 200, response.text
    assert response.json()['locked'] is True
    edit = client.put(f"/staged-plans/{plan['id']}", json=plan_payload('锁定', [(4, '0.02'), (8, '0.02'), (12, '0.02')]))
    assert edit.status_code == 409
    assert '锁定' in edit.json()['detail']


def test_only_next_increment_is_accepted():
    plan = create_plan('增量', [(4, '0.03'), (8, '0.03'), (12, '0.03')])
    wrong = admit(plan['id'], 2, 2, 2, 1, 'increment-key-0001')
    assert wrong.status_code == 422
    assert '仅接收下一阶段增量' in wrong.json()['detail']
    ok = admit(plan['id'], 2, 2, 2, 2, 'increment-key-0002')
    assert ok.status_code == 200
    too_many = admit(plan['id'], 4, 4, 4, 4, 'increment-key-0003', look=2)
    assert too_many.status_code == 422
    assert '应新增4件' in too_many.json()['detail']


def test_repeat_and_concurrent_admission_never_advance_twice():
    plan = create_plan('幂等', [(4, '0.03'), (8, '0.03'), (12, '0.03')])
    first = admit(plan['id'], 3, 1, 3, 1, 'idem-key-0001')
    assert first.status_code == 200 and first.json()['next_look'] == 2
    repeat = admit(plan['id'], 3, 1, 3, 1, 'idem-key-0001')
    assert repeat.status_code == 200
    assert repeat.json()['next_look'] == 2
    assert len(repeat.json()['looks']) == 1
    # A different key for the already-admitted stage is refused, not advanced.
    duplicate = admit(plan['id'], 3, 1, 3, 1, 'idem-key-0002', look=1)
    assert duplicate.status_code == 409
    assert '不得推进阶段' in duplicate.json()['detail']
    body = client.get(f"/staged-plans/{plan['id']}").json()
    assert body['next_look'] == 2 and len(body['looks']) == 1


def _file_database(path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f'sqlite+pysqlite:///{path}', connect_args={'timeout': 30})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_concurrent_repeats_never_double_book():
    import threading
    import tempfile

    from src.models.study import Study as StudyModel
    from src.schemas.staged import StagedLookInput, StagedPlanInput
    from src.services import staged_plans

    with tempfile.NamedTemporaryFile(suffix='.sqlite3') as handle:
        factory = _file_database(handle.name)
        with factory() as db:
            db.add(StudyModel(id=1, name='并发方案', source_filename='t.csv', description=''))
            db.commit()
            plan = staged_plans.create(
                db,
                StagedPlanInput(
                    study_id=1,
                    name='并发重复',
                    looks=[{'n': 4, 'alpha': '0.03'}, {'n': 8, 'alpha': '0.03'}],
                    total_alpha='0.05',
                ),
                ADMIN,
            )
        payload = StagedLookInput(
            look=1, arm_a_success=3, arm_a_failure=1, arm_b_success=3, arm_b_failure=1,
            idempotency_key='concurrent-same-key', note='',
        )
        outcomes = []

        def submit():
            with factory() as db:
                try:
                    staged_plans.admit(db, plan['id'], payload, ADMIN)
                    outcomes.append('ok')
                except Exception as exc:  # noqa: BLE001 - record refusals
                    outcomes.append(type(exc).__name__)

        threads = [threading.Thread(target=submit) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        with factory() as db:
            body = staged_plans.get(db, plan['id'])
        assert body['next_look'] == 2
        assert len(body['looks']) == 1
        assert outcomes.count('ok') >= 1, outcomes


def test_concurrent_distinct_keys_advance_only_one_stage():
    import threading
    import tempfile

    from src.models.study import Study as StudyModel
    from src.schemas.staged import StagedLookInput, StagedPlanInput
    from src.services import staged_plans

    with tempfile.NamedTemporaryFile(suffix='.sqlite3') as handle:
        factory = _file_database(handle.name)
        with factory() as db:
            db.add(StudyModel(id=1, name='并发方案2', source_filename='t.csv', description=''))
            db.commit()
            plan = staged_plans.create(
                db,
                StagedPlanInput(
                    study_id=1,
                    name='并发不同键',
                    looks=[{'n': 4, 'alpha': '0.03'}, {'n': 8, 'alpha': '0.03'}],
                    total_alpha='0.05',
                ),
                ADMIN,
            )
        outcomes = []

        def submit(index):
            payload = StagedLookInput(
                look=1, arm_a_success=3, arm_a_failure=1, arm_b_success=3, arm_b_failure=1,
                idempotency_key=f'concurrent-distinct-{index:04d}', note='',
            )
            with factory() as db:
                try:
                    staged_plans.admit(db, plan['id'], payload, ADMIN)
                    outcomes.append('ok')
                except Exception as exc:  # noqa: BLE001 - record refusals
                    outcomes.append(type(exc).__name__)

        threads = [threading.Thread(target=submit, args=(index,)) for index in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        with factory() as db:
            body = staged_plans.get(db, plan['id'])
        assert body['next_look'] == 2
        assert len(body['looks']) == 1
        assert 'ok' in outcomes, outcomes


def test_stop_is_permanent_and_survives_further_submissions():
    plan = create_plan('停试', [(4, '0.03'), (8, '0.03'), (12, '0.03')])
    stopped = admit(plan['id'], 4, 0, 0, 4, 'stop-key-0001')
    assert stopped.status_code == 200
    body = stopped.json()
    assert body['status'] == 'stopped'
    assert body['stop_look'] == 1
    assert '永久停试' in body['stop_reason']
    assert body['can_admit'] is False
    later = admit(plan['id'], 2, 2, 2, 2, 'stop-key-0002')
    assert later.status_code == 409
    assert '永久停试' in later.json()['detail']
    # Re-submitting the original key replays the stop conclusion instead of failing.
    replay = admit(plan['id'], 4, 0, 0, 4, 'stop-key-0001')
    assert replay.status_code == 200
    assert replay.json()['status'] == 'stopped' and len(replay.json()['looks']) == 1


def test_stopped_plan_cannot_be_edited_into_continuation():
    plan = create_plan('停试锁定', [(4, '0.03'), (8, '0.03'), (12, '0.03')])
    admit(plan['id'], 4, 0, 0, 4, 'stop-edit-0001')
    edit = client.put(f"/staged-plans/{plan['id']}", json=plan_payload('停试锁定', [(4, '0.03'), (8, '0.03')]))
    assert edit.status_code == 409


def test_concurrent_admission_where_one_crosses_keeps_the_stop():
    import threading
    import tempfile

    from src.models.study import Study as StudyModel
    from src.schemas.staged import StagedLookInput, StagedPlanInput
    from src.services import staged_plans

    with tempfile.NamedTemporaryFile(suffix='.sqlite3') as handle:
        factory = _file_database(handle.name)
        with factory() as db:
            db.add(StudyModel(id=1, name='并发停试方案', source_filename='t.csv', description=''))
            db.commit()
            plan = staged_plans.create(
                db,
                StagedPlanInput(
                    study_id=1,
                    name='并发停试',
                    looks=[{'n': 4, 'alpha': '0.03'}, {'n': 8, 'alpha': '0.03'}],
                    total_alpha='0.05',
                ),
                ADMIN,
            )
        payloads = [
            StagedLookInput(look=1, arm_a_success=4, arm_a_failure=0, arm_b_success=0, arm_b_failure=4,
                            idempotency_key='cross-key-a', note=''),
            StagedLookInput(look=1, arm_a_success=2, arm_a_failure=2, arm_b_success=2, arm_b_failure=2,
                            idempotency_key='cross-key-b', note=''),
        ]
        outcomes = []

        def submit(payload):
            with factory() as db:
                try:
                    result = staged_plans.admit(db, plan['id'], payload, ADMIN)
                    outcomes.append(result['status'])
                except Exception as exc:  # noqa: BLE001 - record refusals
                    outcomes.append(type(exc).__name__)

        threads = [threading.Thread(target=submit, args=(payload,)) for payload in payloads for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        with factory() as db:
            body = staged_plans.get(db, plan['id'])
        assert body['next_look'] == 2
        assert len(body['looks']) == 1
        assert body['status'] in ('stopped', 'active')
        if body['looks'][0]['decision'] == 'stop':
            assert body['status'] == 'stopped'
            assert '永久停试' in body['stop_reason']


def test_submission_declaring_a_later_stage_is_refused_after_stop():
    plan = create_plan('停试撞下一阶段', [(4, '0.03'), (8, '0.03')])
    stopped = admit(plan['id'], 4, 0, 0, 4, 'collide-key-0001', look=1)
    assert stopped.json()['status'] == 'stopped'
    collide = admit(plan['id'], 2, 2, 2, 2, 'collide-key-0002', look=2)
    assert collide.status_code == 409
    assert '永久停试' in collide.json()['detail']


def test_final_look_completes_when_never_crossing():
    plan = create_plan('末次', [(2, '0.03'), (4, '0.03')])
    first = admit(plan['id'], 1, 1, 1, 1, 'final-key-0001', look=1)
    assert first.status_code == 200 and first.json()['status'] == 'active'
    second = admit(plan['id'], 1, 1, 1, 1, 'final-key-0002', look=2)
    assert second.status_code == 200
    body = second.json()
    assert body['status'] == 'completed'
    assert '末次' in body['stop_reason']
    after = admit(plan['id'], 1, 0, 0, 1, 'final-key-0003', look=2)
    assert after.status_code == 409


def test_both_arms_are_booked_atomically():
    plan = create_plan('原子', [(4, '0.03'), (8, '0.03'), (12, '0.03')])
    response = admit(plan['id'], 2, 2, 2, 2, 'atomic-key-0001')
    body = response.json()
    look = body['looks'][0]
    assert look['cumulative']['A'] == {'success': 2, 'failure': 2}
    assert look['cumulative']['B'] == {'success': 2, 'failure': 2}
    assert look['increments']['A'] == {'success': 2, 'failure': 2}
    assert look['increments']['B'] == {'success': 2, 'failure': 2}


def test_cumulative_fisher_matches_reference():
    from src.services import staged as engine_module
    from scipy.stats import fisher_exact

    cases = [(3, 1, 1, 3), (0, 4, 4, 0), (4, 4, 4, 4), (2, 2, 3, 1), (1, 3, 2, 2)]
    for a, b, c, d in cases:
        exact = engine_module.fisher_two_sided_exact(a, b, c, d)
        reference = fisher_exact([[a, b], [c, d]], alternative='two-sided').pvalue
        assert abs(float(exact) - reference) < 1e-12, (a, b, c, d, exact, reference)


def test_calibration_matches_direct_enumeration():
    from fractions import Fraction
    from math import comb

    from src.services import staged as engine_module

    looks = [{'n': 4, 'alpha': '0.01'}, {'n': 8, 'alpha': '0.02'}, {'n': 12, 'alpha': '0.03'}]
    normalized = engine_module.normalize_looks(looks)
    key = tuple((item['n'], item['alpha']) for item in normalized)
    sizes = [n for n, _ in key]
    flags = [engine_module._crossing_flags(n, alpha) for n, alpha in key]

    def paths(sizes):
        out = [((), 1)]
        previous = 0
        for n in sizes:
            delta = n - previous
            expanded = []
            for sequence, weight in out:
                base = sequence[-1] if sequence else 0
                for step in range(delta + 1):
                    expanded.append((sequence + (base + step,), weight * comb(delta, step)))
            out, previous = expanded, n
        return out

    arms = paths(sizes)
    for p in (Fraction(1, 10), Fraction(1, 2), Fraction(3, 4), Fraction(9, 10)):
        total = Fraction(0)
        weights = {}
        for arm, _ in arms:
            probability, previous_n, previous_success = Fraction(1), 0, 0
            for index, n in enumerate(sizes):
                delta = n - previous_n
                step = arm[index] - previous_success
                probability *= Fraction(comb(delta, step)) * p ** step * (1 - p) ** (delta - step)
                previous_n, previous_success = n, arm[index]
            weights[arm] = probability
        for arm_a in weights:
            for arm_b in weights:
                for index in range(len(sizes)):
                    if flags[index][arm_a[index]][arm_b[index]]:
                        total += weights[arm_a] * weights[arm_b]
                        break
        assert engine_module.type1_probability(looks, p) == total, p


def main():
    failures = 0
    for name, function in sorted(globals().items()):
        if not name.startswith('test_') or not callable(function):
            continue
        try:
            function()
            print(f'PASS {name}')
        except Exception as exc:  # noqa: BLE001 - report every failure
            failures += 1
            print(f'FAIL {name}: {type(exc).__name__}: {exc}')
    print('failures:', failures)
    return failures


if __name__ == '__main__':
    sys.exit(main())
