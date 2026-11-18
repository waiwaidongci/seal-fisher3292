# 验证记录

本文件记录实际执行过的验证。运行环境为本地 Python 3.11（无 Docker 时的后端单测），命令均在 `backend/` 下执行。

## 单元与接口测试

`python3 tests/test_staged_api.py`（内存 SQLite、依赖注入替代登录、无需 MySQL/Redis）：

|测试|覆盖点|结果|
|---|---|---|
|test_preview_is_exact_and_rejects_grid_substitute|校验报告使用精确方法；阈值之和仅作对照且不参与判定|通过|
|test_over_limit_schedule_is_refused|最大误拒概率超过总限额时拒绝启用|通过|
|test_exact_max_catches_peak_a_coarse_grid_misses|某计划峰值在20点网格上被低估 1e-5 以上，精确判定仍按真实最大值拒绝|通过|
|test_invalid_schedules_are_refused|0/负样本、超过12件、累计不递增、超过3次、阈值0或1、总限额0|通过|
|test_plan_locks_after_first_look|首批入账后锁定，不能修改预登记|通过|
|test_only_next_increment_is_accepted|只接收下一阶段增量；错误增量与超量增量拒绝|通过|
|test_both_arms_are_booked_atomically|A/B 两组增量与累计同时入账|通过|
|test_repeat_and_concurrent_admission_never_advance_twice|同幂等键回放；同阶段不同键不推进|通过|
|test_concurrent_repeats_never_double_book|6 线程同键提交（文件库、独立会话）只落一笔阶段|通过|
|test_concurrent_distinct_keys_advance_only_one_stage|6 线程不同键并发（文件库）只有一笔生效|通过|
|test_stop_is_permanent_and_survives_further_submissions|首次越界永久停试；后续增量被拒；原键回放停试结论|通过|
|test_stopped_plan_cannot_be_edited_into_continuation|停试后不能把计划改回继续|通过|
|test_final_look_completes_when_never_crossing|始终未越界则末次结束，之后不再接收|通过|
|test_concurrent_admission_where_one_crosses_keeps_the_stop|并发提交中若一笔越界，最终只落一笔且在停试时保留停试结论|通过|
|test_submission_declaring_a_later_stage_is_refused_after_stop|停试后声明下一阶段的提交被拒，不能撞开新阶段|通过|
|test_worker_recovery_keeps_stop_conclusion|worker 恢复只重排一次性运行，停试结论与阶段记录不变|通过|
|test_cumulative_fisher_matches_reference|累计2×2双侧Fisher与 scipy `fisher_exact` 一致|通过|
|test_calibration_matches_direct_enumeration|拒绝概率多项式在多个 p 上与逐路径精确枚举完全相等|通过|

## 精确性与边界（开发期额外核对）

- 拒绝概率多项式在 p=0.1/0.5/0.75/0.9 与逐路径二项枚举逐项相等（Fraction 精确比较）。
- 最大值判定与总限额的严格边界：限额恰等于 F(1/2) 时判为通过；比它小 1e-18 时判为超过。说明判定是精确的，不是浮动阈值比较。
- 随机整系数多项式 300 例：精确正性判定未漏掉任何抽样可见的正值；随机多项式 120 例：Bernstein 上界从不低于 5000 点抽样最大值。
- 最坏三查看计划（n=4/8/12）单次校验 0.02–0.2 秒，证书宽度 ≤1e-12。

## 未在本环境执行

- Docker Compose 全栈启动、MySQL 8 与 Redis 7 集成、Nginx 前端加载：本机无 Docker 与对应服务，未执行。
- 真实 MySQL 下的并发入账：SQLite 不支持 `SELECT ... FOR UPDATE` 的阻塞语义，并发用例改用文件库+独立会话验证「最终只有一个阶段、响应不会500」；MySQL 行锁行为未在本环境实机复跑，由行锁与唯一约束设计保证。
- 浏览器端交互：仅做 ES module 语法与相对导入解析检查，未做真实浏览器走查。
