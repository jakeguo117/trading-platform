# Trading Platform｜GLD R2 Theta Qualification 规范

**Last Updated:** 2026-09-03

**状态：** `RESEARCH_ONLY / LOCAL_SYNTHETIC_INFRASTRUCTURE / NO_ACTIVE_THETA`

本规范落实 Jake 已批准的 R2 完整计划。R2 的唯一目标是：以 R1 已冻结的规则为输入，用 Development、历史 Walk-forward 和一次性 Forward 证据淘汰不可靠政策，最终诚实输出零个或最多一个待 Jake 决定的研究 `theta`。

R2 不接入 Daily Shadow，不建设 R3，不连接真实账户，不产生交易指令。任何本地输出都必须保持：

```text
RESEARCH_ONLY
actionable = false
broker_order_count = 0
```

## 1. Roadmap 与完成定义

```text
R1：冻结候选规则与确定性决策语义
 -> R2：以历史和 Forward 证据筛选 0/1 个研究 theta
 -> R3：Daily Shadow 验证
 -> 后续：Owner 控制的人工执行与只读核对
```

R2 的合法业务终局只有：

- 所有候选在充分证据下失败：`NO_QUALIFIED_POLICY`；
- 最多一个候选通过机器资格：`QUALIFIED_POLICY_PENDING_OWNER`；
- Jake 对精确 qualification hash 明确拒绝或批准。

`DATA_NOT_QUALIFIED` 与 `INSUFFICIENT_EVIDENCE` 不是策略失败，只关闭当前 generation 并使 R2 保持 blocked。只有 `NO_QUALIFIED_POLICY`、`OWNER_REJECTED_R2_RESEARCH_THETA` 或 `OWNER_APPROVED_R2_RESEARCH_THETA` 关闭 R2。Owner 批准也只解锁 R3 规划，不产生 Active theta、部署或交易权限。

## 2. 冻结候选目录

候选维度固定为：

```text
完整政策包 = Gate x Hard Stop x R1 其余固定规则
ordinal = gate_index * 4 + stop_index
candidate_id = R2C{ordinal:02d}
```

Gate 顺序直接复用 R1 的权威 catalog：

```text
G0 = Trend 3/3 + Breakout 2/2
G1 = Trend 2/3 + Breakout 2/2
G2 = Trend 3/3 + Breakout 1/2
G3 = Trend 2/3 + Breakout 1/2
```

Hard Stop 顺序：

```text
S0 = 333333 ppm
S1 = 500000 ppm
S2 = 666667 ppm
S3 = DISABLED_CONTROL / null
```

| Gate | 333333 | 500000 | 666667 | no-stop control |
|---|---:|---:|---:|---:|
| G0 | R2C00 | R2C01 | R2C02 | R2C03 |
| G1 | R2C04 | R2C05 | R2C06 | R2C07 |
| G2 | R2C08 | R2C09 | R2C10 | R2C11 |
| G3 | R2C12 | R2C13 | R2C14 | R2C15 |

因此：

```text
promotable = {0,1,2,4,5,6,8,9,10,12,13,14}
controls   = {3,7,11,15}
```

四个 no-stop controls 只用于诊断；它们不得进入 Development 晋升、`S_dev`、联合统计 family、champion 或 Owner 候选。LC0 与 BCS0 是每个候选内部的独立证据路径，不构成新的候选搜索维度。

所有候选固定 `q=1`。本代不搜索账户 sizing、bankroll 或 drawdown。Delta selectors、Preference、H20、Confirmed Invalidation、expiry safety 与 Management F0 引用来自 R1 fixed projection；R2 不得修改正式 R1 资产来表达这些冻结语义。

## 3. 非循环身份链与盲测冻结

身份必须单向派生，结果或 Owner 决定不得反向改变政策身份：

1. `base_candidate_sha256` 绑定 candidate ordinal、Gate、Hard Stop、`q=1` 和 R1 fixed projection。
2. `development_method_freeze_sha256` 必须在读取任何 Development outcome 前生成，绑定 registry、partition manifest、fee manifest、exit/stress method、estimator、code package 与 runtime。
3. `evaluated_candidate_sha256` 绑定 base candidate、Development freeze、Development-only 决定的 carrier mode，以及 LC0/BCS0 两份 Development receipt。即使冻结为 ONLY，失败的 sibling receipt 也不能省略。
4. `research_freeze_sha256` 必须在读取任何 WF outcome 前生成，绑定有序 `S_dev`、共同 opportunity manifest、联合 estimator、code package 与 runtime。
5. `historical_theta_sha256` 只绑定冻结 candidate 和 WF-only carrier/policy receipts，不把 Development outcome 混入最终估计。
6. `qualification_sha256` 绑定历史 theta、Forward receipt 与机器终局；Owner 决定不在其中。
7. `owner_decision_receipt_sha256` 单独绑定 qualification hash 和人工决定。纯 hash helper 不是身份认证或 Owner 授权。

任何 freeze 所绑定的规则、数据区间、费用、代码或 runtime 变化都必须建立新 generation，不能覆盖旧 receipt。

Metadata 层只能形成 schema、时间、来源身份、日期、计数和 coverage receipt。任何会暴露价格、return、exit path 或候选比较结果的 bytes，在对应 outcome access 获批前必须不可读。

## 4. Outcome-blind 时间轴与共同 Mask

在 Technical Facts warm-up 后冻结共同 XNYS session 序列。设：

```text
usable_entry = total_after_warmup - 6 * 20
base = usable_entry // 6
remainder = usable_entry % 6
```

若 `base <= 0`，返回 `INSUFFICIENT_EVIDENCE`。固定半开窗口为：

```text
DEV_ENTRY(base) -> DEV_LABEL20
WF1_ENTRY(base) -> WF1_LABEL20
WF2_ENTRY(base) -> WF2_LABEL20
WF3_ENTRY(base) -> WF3_LABEL20
WF4_ENTRY(base) -> WF4_LABEL20
WF5_ENTRY(base + remainder) -> WF5_LABEL20
```

- 只在 ENTRY window 开仓；LABEL20 只完成最迟 H20 标签，不开新仓，也不进入 estimator 或 coverage denominator。
- episode return 在关闭后回填 entry session row，不记在 exit row。
- 某 fold 的训练 evidence 只允许使用 `exit_utc_ns < 该 fold ENTRY start` 的 episode。
- 所有候选使用完全相同的窗口。

`CommonJointRowMaskV1` 必须在策略运行前、以 candidate-independent 且 outcome-blind 的方式冻结。共同 coverage 为合格 ENTRY rows 除以全部 ENTRY rows，必须至少 95%。

- common 数据缺失行不进 estimator，但仍留在 coverage denominator；
- Gate fail、合法无结构、no-action 和持仓期间 session 是可评价的 0；
- 不得因某个 candidate 缺失而 candidate-wise 删行；
- mask 内任一 candidate 出现 `UNEVALUABLE`，整个 generation 为 `INSUFFICIENT_EVIDENCE`。

## 5. Development 与 carrier mode

每个 promotable base candidate 分别维护 LC0 和 BCS0 两条 counterfactual `q=1` ledger，每条最多一个开放 episode。每个 carrier 只有同时满足以下条件才是 sufficient：

- 至少 100 个完整 Development episodes；
- Gate-triggered reconstruction coverage 至少 95%。

充分后才可运行固定 episode-level `CIRCULAR_MBB_KELLY_V2`：

```text
replicates = 10000
block_length = 20 completed episodes
LCB = sorted(bootstrap_means)[499]
RobustFullKelly = min(raw Full Kelly, sorted(bootstrap_kelly)[499])
HalfKelly = RobustFullKelly // 2
pass iff LCB > 0 and HalfKelly > 0
```

Carrier mode 映射固定为：

- 两条 sufficient 且通过：`DUAL_PREFERENCE`；
- 两条 sufficient，一条通过、一条失败：`LC0_ONLY` 或 `BCS0_ONLY`；
- 两条 sufficient 且失败：`DEVELOPMENT_REJECTED`；
- 任一 carrier 不 sufficient 或 unknown：`INSUFFICIENT_EVIDENCE`。

Unknown 不能当成 fail，也不能被用来事后降级成 ONLY。只有 12 个 promotable candidates 全部获得充分分类后，才可冻结 `S_dev` 和 `K=|S_dev|`。若 12 个均充分失败，合法终局为 `NO_QUALIFIED_POLICY / ALL_DEVELOPMENT_REJECTED`；只要存在 unknown/insufficient，就不能进入 WF。

## 6. Walk-forward 与联合政策检验

每个 evaluated candidate 同时维护 frozen-mode 所要求的 carrier ledgers 和一条实际 policy ledger：

- 实际 ledger 固定 `q=1`、最多一个开放 episode；
- `LC0_ONLY/BCS0_ONLY` 只运行冻结 carrier；
- `DUAL_PREFERENCE` 只能使用当前 test window 开始前已经关闭的训练 evidence 和 R1 Preference；当前 fold outcome 不能改变当前 fold 选择；
- backup 永不自动执行；
- WF 后不得缩小 `S_dev` 或改变 carrier mode。

每个冻结 carrier 必须具有至少 100 个完整 WF-OOS episodes、五个非空 folds、至少 95% coverage、episode-level LCB 大于 0 且 Robust Half-Kelly 大于 0。DUAL 任一 carrier 充分失败则整个 candidate 失败；出现 unknown/insufficient 则停止 generation。

政策矩阵固定为共同 WF ENTRY rows 乘有序 `S_dev` columns。联合检验在每个 fold 内做同步 circular moving-block resampling，相同 replicate 与 block ordinal 对全部 K columns 使用同一起点，然后以固定 row count 合并各 folds。

主资格方法固定：

```text
replicates = 10000
block_length = 20 sessions
m_j = candidate j 的原始全 WF 共同-session mean
m*_{bj} = replicate b 中 candidate j 的全 WF mean
E_{bj} = m*_{bj} - m_j
D_b = max_j(E_{bj})
c95 = sorted(D)[9499]
simultaneous_LCB_j = m_j - c95
pass iff simultaneous_LCB_j > 0
```

`E = m* - m` 的方向、`9499` 的 zero-based rank 和 frozen K family 都属于合同，不能由实现自行翻转或缩小。Policy path 不增加 Kelly gate。Block 10/40 只能输出 report-only sensitivity，不得改变 pass/fail 或 champion。

Champion 是同时通过 frozen carrier gates 与 stressed simultaneous LCB 的候选中 LCB 最大者；完全相同时取较小 ordinal。结果必须是 0 或 1。

## 7. R2 Exit/Stress 目标合同与费用边界

以下是获批的 R2 research 目标语义，不改写 R1：

- 每个 XNYS session 只使用首张合格 `[10:45,10:46)` PIT 原子快照；
- tick 来自当时的 contract minimum price increment；缺失或不明确即数据不足；
- LC entry 为 `ask + 1 tick`，exit 为 `max(0, bid - 1 tick)`；
- BCS long/short opening 和 closing legs 均按不利一 tick 处理；
- opening 与 closing side fees 全部计入；
- 退出优先级为 `HARD_STOP > CONFIRMED_INVALIDATION > H20 > HOLD`；
- stressed after-cost path 是资格与排名主路径，base BBO 只报告。

当前没有获批任何真实 fee authority。`fee_schedule_manifest_sha256` 字段只是一项冻结接口，不能证明费用来源已批准或费用计算已实现。真实数据 gate 必须逐项列出并绑定 effective-dated broker、exchange、clearing 和 regulatory fee source，经 Jake 另行批准后才可用于 Development、WF 或 Forward。

## 8. Exact-100 Forward Sealed

真实 Forward 的目标合同以冻结 historical champion 为唯一候选：

```text
T0 = max(historical champion freeze receipt time,
         historical maximum outcome time)
```

只允许 T0 后的新数据。预注册恰好 100 个 terminal policy episodes；第 100 个关闭时自动 no-clobber seal，第 101 个及之后不属于本 generation。Unknown episode 或 coverage 低于 95% 都在第 100 个 slot 关闭为 `INSUFFICIENT_EVIDENCE`，不得通过延长样本修复。失败后不得用同一 Forward 区间测试 runner-up。

Reveal 后的唯一 hard gate 为：

```text
block_length = 20 sessions
replicates = 10000
Forward_LCB = sorted(bootstrap_means)[499]
pass iff Forward_LCB > 0
```

Forward 不重新估计 Kelly 或 theta；block 10/40 仍只报告。100 是预注册门槛，不是 power 或未来盈利保证，可能需要很长时间。

**当前本地 Forward 实现严格是 `SYNTHETIC_ONLY` 的状态机和 diagnostic container。** 它可以验证连续 session、H20、entry-row attribution、第 100 个自动 seal、第 101 个拒绝、unknown/coverage fail-closed、canonical bytes 与 tamper rejection，但不能采集真实 Forward 数据，不能生成真实 qualification，也不能替 Jake 产生 Owner receipt。本地文件 hash 是可审计完整性控制，不是针对拥有文件系统权限者的防复制或身份认证边界。

## 9. 状态与授权合同

外层运行状态只有：

```text
RUN_SUCCEEDED
RUN_FAILED
```

`RUN_FAILED` 只说明程序、输入、资源、I/O 或发布失败，不得制造或覆盖量化结论。

主要中间状态：

```text
R2_INFRASTRUCTURE_VERIFIED
METADATA_AUTHORIZATION_REQUIRED
DEVELOPMENT_OUTCOME_AUTHORIZATION_REQUIRED
WF_OUTCOME_AUTHORIZATION_REQUIRED
PENDING_FORWARD_SEALED
SEALED_REVEAL_AUTHORIZATION_REQUIRED
QUALIFIED_POLICY_PENDING_OWNER
```

Generation terminals：

```text
DATA_NOT_QUALIFIED
INSUFFICIENT_EVIDENCE
NO_QUALIFIED_POLICY
OWNER_REJECTED_R2_RESEARCH_THETA
OWNER_APPROVED_R2_RESEARCH_THETA
```

当前本地 terminal receipt 只允许它实际到达的 machine-negative terminal，并且只绑定该 stage 已存在的 hashes；不得为未运行的未来 stage 填伪造占位值。Owner approve/reject 必须由另行授权的追加式人工 receipt 表达，不能由 synthetic runner 或 identity helper 签发。

本计划的本地实施授权不包含 provider/API、credentials、网络服务、真实数据、下载或费用、outcome access、Forward collection/reveal、stage、commit、push、merge、部署或交易。每个外部阶段都必须提交精确授权包，列明 provider、schema、日期、许可、bytes/cost、保存位置、credential scope 与费用来源。

## 10. 当前 Wave 1 实现边界

Wave 1 只建设可本地验证的基础设施：closed/canonical contracts、16-candidate registry、non-cyclic identities、Development disposition、盲测窗口和 common mask、deterministic statistics receipts、stage-aware negative terminals、synthetic Exact-100 container，以及相应 fixtures、测试与 Owner 可见证据。

无论本地测试数字多少，Wave 1 都不能声称已经完成：

- 真实 provider 或数据资格；
- effective-dated 费用批准；
- 真实 Development/WF/Forward replay；
- 真实 after-cost 政策比较；
- 真实 `NO_QUALIFIED_POLICY` 或 qualified theta；
- Owner approval、Active theta、R3、broker 或交易。

本地 Wave 1 的正确停止位置是：

```text
R2_INFRASTRUCTURE_VERIFIED
METADATA_AUTHORIZATION_REQUIRED
```

## 11. R1 不可变基线与 fresh regeneration 说明

R1 接受基线固定为 commit `b21400d799f7b441d35f528a5980b1ce855997a7`。以下正式资产必须相对该 commit 完全无差异：

- `artifacts/gld-entry-decision-f0-owner-acceptance-v1/`，45 个文件；
- `2026-09-01-2157-gld-entry-decision-f0-owner-acceptance-receipt-v0.1.md`。

关键固定 SHA-256：

| 资产 | SHA-256 |
|---|---|
| acceptance manifest file | `f0004cdb593121f1cb41f632f4b36c3399fe5d380d938e909f0cd4bcf02141a2` |
| Owner index | `5590a21a5fbccc34a1ce495484f9ec16b8c29c89b10def6349c59ed6d28aa18b` |
| engineering evidence | `6a96bcbacd7d121bffa2fcf5b6d3f243096936181c0bbc9d431032adf14ec35f` |
| accepted reference spec | `240c6dc5df135c1062db067ad6c8e215fa39a041403a488e1aed32c0f9d83c81` |
| accepted reference policy asset | `1541636445c029602f7b0356b03b4149a4e8b7fb1cda545dd8b5664218ed082e` |
| append-only Owner receipt | `e894aa5777a5f7b2179eb4dc3ebd8b80f12c3e80d74c2c2adca312cdb3d36231` |

从当前 source docs 做 fresh R1 regeneration 时，已知并预期恰好出现两个文件差异：

1. `reference/GLD_ENTRY_POLICY_RESEARCH_ASSET_F0.md`：正式验收目录保留 Jake 批准前的快照；批准后，源 `docs/GLD_ENTRY_POLICY_RESEARCH_ASSET_F0.md` 追加了 `OWNER_ACCEPTED_COMPLETE` 和人工验收说明，当前 SHA 为 `86d582c49d60ae2558c3f962fe689a5d3c3b2a524a3523883cb8bf9c2eeb8b51`。
2. `acceptance-manifest.json`：只因上述 reference asset hash 与 manifest 自身 hash 连带变化。

Fresh tree 的其他 43 个文件必须与正式验收目录逐字节一致。这两个已解释差异不是 R2 回归；反过来，也不得用 fresh regeneration 覆盖正式 R1 验收快照。

## 12. 本地验收

R2 定向测试必须覆盖 registry、identity/freeze、窗口/mask、unknown 与 failure、carrier/joint estimator、Forward state 和 stage receipts；独立 R1 regression 还必须验证正式验收树与 Owner receipt 相对接受 commit 零差异，并验证上述 fresh regeneration 仅有两个已知差异。

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_gld_r2_r1_regression -v

PYTHONPATH=src python3 -m unittest discover -s tests -v
```

这些测试只证明本地 synthetic 基础设施、确定性和失败边界，不能证明策略盈利或真实数据、费用、账户、broker 和 Owner authority 已资格。

## 13. 2026-09-03 Wave 0–1 Owner 人工验收记录

Jake 原话：

> 我批准 R2 Wave 0–1 本地基础设施验收。

记录结论：Wave 0–1 本地 synthetic 筛选基础设施已获 Owner 接受。接受依据为 6 / 6 个可见场景与程序结果一致、R2 专项 118 / 118、全仓 737 / 737，以及独立代码审查和安全审查均通过且无 blocker。写入回执时重新运行了 R2 专项；全仓回归和两项独立审查是 Jake 本次批准所依赖的既有已验证证据，没有因为只新增文档记录而重复运行。

追加式人工回执：

- 路径：`2026-09-03-gld-r2-wave0-1-local-infrastructure-owner-acceptance-receipt-v0.1.md`
- SHA-256：`cb1d5757358e570083c7ac36aa120a37930c7e41a61d7a79cebfb7caa58a27c0`

该回执精确绑定验收前的三项可见证据：

| 资产 | SHA-256 |
|---|---|
| `artifacts/gld-r2-infrastructure-acceptance-v1/acceptance-manifest.json` | `5104745484e17422d05fb448516eddd4082db00c94d4c782b568e0f9c492318d` |
| `artifacts/gld-r2-infrastructure-acceptance-v1/index.html` | `c443e8b1220a0ac6e478420f4348fc3080b9541155fb2ce8be3355456f718e16` |
| `artifacts/gld-r2-infrastructure-acceptance-v1/engineering-evidence.html` | `cb853d9b62621ffd4e0d9c7251793c035e10fe1a249dc127437e7163e9849e6d` |

自动生成的 manifest 继续保持 `OWNER_ACCEPTANCE_PENDING / owner_receipt_generated=false`，因为它是验收前不可变工程证据，不能由程序回写成人工签字。Owner 接受只记录在独立追加式回执中。

本次接受不关闭 R2。机器状态与下一道门仍为：

```text
R2_INFRASTRUCTURE_VERIFIED
METADATA_AUTHORIZATION_REQUIRED
```

全部产物继续保持 `SYNTHETIC_ONLY / RESEARCH_ONLY / NO_DECISION_EFFECT / actionable=false / broker_order_count=0`；真实数据访问和真实 `theta` 数量均为零。本次接受不授权 metadata、provider、费用、历史 outcome、Forward collection/reveal、R3、部署、交易或任何 Git 保存动作。
