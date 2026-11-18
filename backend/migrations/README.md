Schema is defined by the SQLAlchemy models and created at startup via Base.metadata.create_all. For this local small application no destructive migration is run. Back up named db-data before modifying schemas.

Version 2 adds the pre-registered staged seal-trial tables:

* `staged_plans` — one frozen look schedule (cumulative n per arm <= 12 and per-look threshold for <= 3 looks), the stored exact calibration report, lock time, and the permanent stop verdict (`status`, `stop_look`, `stop_reason`, `stop_p`).
* `staged_looks` — one committed stage increment for both arms, with the cumulative table, exact Fisher p, threshold and verdict. `UNIQUE(plan_id, look)` and `UNIQUE(plan_id, idempotency_key)` make a repeated or concurrent submission a replay, never a second advance.

Existing `studies`, `observations`, `runs`, `reviews` and `audits` are unchanged, so one-off Fisher studies keep working. If upgrading an existing database, create the two new tables (`Base.metadata.create_all` does this) before using the staged page; no column of the old tables changed.
