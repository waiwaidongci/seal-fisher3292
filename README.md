# 砚田包装试封比较席

两种封口工艺独立合格/失败计数→固定方案比较→复试决策；双侧Fisher按概率不大于观测表求和。原生 HTML/CSS/JavaScript 多模块工作席，所有计算由 Python 完成。

## 模拟业务
砚田研究协作组，12名分析师与业务人员，日常使用 `seal-fisher-202611-1.csv` 手工抄表。当前三个痛点：观测与口径不可追溯、重算易出错、结果与复核割裂。上游提供独立合成业务观测；本应用接管数据登记、方法执行和输入冻结；下游负责人形成复测/预算方案，观察员获取复核后的CSV。

核心业务实体：SealTrial / TrialCount / ExactRun / TrialDecision。源码 model/schema/service/controller/route 与前端 api/store/pages 完成链路，不依赖浏览器本地假数据。

## 功能与方法
- 试封方案、工艺计数录入/编辑/删除；重名记录与非法数值明确拒绝。
- 分阶段试封（预登记）：每组至多12件、至多3次查看，逐次登记累计样本量与各次阈值，首批入账后锁定。启用前精确计算两工艺共同合格率在0到1间的最大误拒概率；超过总限额即拒绝启用。每阶段按双侧Fisher对累计2×2表判定，首次越界永久停试，否则到末次结束。只接收下一阶段增量，A/B两组同一事务入账；重复或并发提交只回放既有结论、不重复推进；worker中断恢复保留停试结论。页面显示计划校验、阶段依据与停试原因。旧的一次性Fisher研究完整保留。
- Fisher精确检验、Wilson单比例区间与率差。两组试件独立，组别预先固定。双侧 p 值是固定边际下概率不大于观察表的所有表之和。
- 每次运行冻结参数、观测和SHA256。Redis任务由独立worker计算，写DB与共享产物卷；输入修改不会改写历史结果。
- 负责人复核不可覆盖；审计记录登录、数据改动、排队、复核与导出；240请求/分钟限流、全局可恢复错误。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18241 （“分阶段试封”页在导航第6项），API http://localhost:19241/docs 。数据库映射 17741。无需Node构建。可以从中文和带空格目录启动。清理 `docker compose down -v --remove-orphans` 会删除本题演示数据卷。

|角色|账号|密码|权限与典型动作|
|---|---|---|---|
|admin 负责人|admin@demo.local|Research2026!|全部读写、批准复核、下载|
|analyst 分析师|analyst@demo.local|Research2026!|数据修改、创建任务、读取结果及下载；不能复核|
|viewer 观察员|viewer@demo.local|Research2026!|只读；仅可导出已复核结果|

seed 另建两个分组序贯示例：`砚田·薄膜分阶段三轮`（已完成2次查看，等待第3次）与 `砚田·覆膜分阶段两轮`（第1次越界、已永久停试）。

## 一天的工作剧本
1. 09:00 admin登录，查“砚田·薄膜一轮”。
2. 09:10 analyst核对 `seal-fisher-202611-1.csv` 的seed记录。
3. 09:15 来源录入页检查 `工艺组 (A/B)` 等业务字段，必要时编辑。
4. 09:20 分析执行页保留默认参数 `{"alpha": 0.05}`，命名本轮研究。
5. 09:21 冻结输入并排队，worker实算，结果页自动刷新。
6. 09:25 阅读核心数值、方法边界、明细与SHA256；发现失败先修数据重跑。
7. 09:35 admin打开业务复核，写明限制与下一步动作，归档后不可覆盖。
8. 09:40 viewer读取复核意见并下载CSV，在操作留痕页核对下载事件。

seed提供3次试验×2个工艺组，共6条汇总计数，代表600份独立试件。每次运行每组只允许一条预聚合记录；修正计数应编辑原记录，重复组会明确失败。

## 技术与目录
|部分|实现|
|---|---|
|前端|原生ES modules、HTML/CSS、Nginx1.27，无前端框架|
|API|Python3.11、FastAPI0.115、SQLAlchemy2、JWT、PBKDF2密码|
|计算|NumPy2.2、SciPy1.15，固定seed42|
|数据库|MySQL8|
|任务|Redis7持久队列，Python独立worker，心跳healthcheck|

`frontend/src/`分api、stores、types、components/common、hooks、pages、router、utils、constants；`backend/src/`分config、models、schemas、services、controllers、routes、middlewares、utils、constants；`backend/scripts/seed_demo.py`自然键插入、重复不覆盖修改；`backend/migrations/README.md`说明schema版本。

环境变量 `.env.example`：COMPOSE_PROJECT_NAME、FRONTEND_PORT、BACKEND_PORT、DB_PORT、DB_PASSWORD、JWT_SECRET、SEED_DEMO。开发时在本地venv安装backend/requirements.txt，设置DATABASE_URL、REDIS_URL、JWT_SECRET再运行uvicorn；前端开发经Nginx代理 `/api/` 到不带前缀的API。

共享枚举：角色admin/analyst/viewer和队列queued/running/completed/failed；后端`src/constants/enums.py`、认证与worker使用相同值；前端`src/types/enums.js`、状态与角色界面使用相同值。Google Fonts：Noto Serif SC + IBM Plex Mono；断网使用serif/monospace回退。

## 分阶段试封的计划校验
设两工艺共同合格率为 p∈(0,1)，第 k 次查看在每组累计 n_k 件时对累计2×2表做双侧Fisher，p 值不大于该次预登记阈值即停试。先在精确有理数下把「停试概率」写成 p 的多项式：只有首次越界的路径计入，且只累计到越界那次查看的增量；再用 Bernstein 有理包围求其在 [0,1] 上的最大值的上下界，并用 Sturm 根隔离精确判定最大值是否严格大于总限额。全过程不做网格抽样，也不以各次阈值相加代替。校验报告同时给出精确上/下界、取到最大值的 p、证书宽度，以及仅作对照的阈值之和（明确不用于判定）。

停试与推进的持久化：每次查看的增量、累计表、精确 p、阈值与结论和计划行在同一事务提交。`UNIQUE(plan_id, look)` 与 `UNIQUE(plan_id, idempotency_key)` 保证同一幂等键只回放、不同键的并发提交只有一笔生效；计划行先取行锁再读，避免可重复读快照漏掉并发已提交的阶段。停试后不再接受增量，也不能把计划改回继续；worker 重启只恢复一次性运行，不触碰分阶段计划。

## 算法输入、失败态与复现
输入为选定批次全部观测和参数，输出核心数值、明细、解释；参数经后端严格白名单和范围验证。两组试件独立，组别预先固定。双侧 p 值是固定边际下概率不大于观察表的所有表之和。。空批次拒绝；不完整字段/非有限数/非法计数拒绝；常量相关列与不足样本使任务failed并保留错误。固定seed42、输入checksum相同可复现。独立oracle：2×2固定边际对scipy fisher_exact；零格与计数非法拒绝。

已知边界：分阶段试封只用于两组独立、组别预先固定、累计样本量与阈值预先登记的方案；不自动选择时序，不提供期中重估样本量。小型本地研究应用，使用合成数据，不含企业SSO与外发邮件；结果由人工复核，不自动执行业务决策；单worker恢复queued/running任务，数据库与Redis间未采用分布式事务。以当前测试目录TESTING.md记录实际验证结果。

MIT License。
