# Trading Platform — GLD Deterministic Research Core

当前仓库保存 GLD 交易系统的版本化研究合同、历史依据、HTML workflow demo，以及本地确定性 reference implementation。

当前可验证状态是：`LOCAL_SYNTHETIC_RESEARCH_CORE` 加 `RAW_SIMULATION_DECISION_CARD`。后者能够从无预制答案的 raw synthetic bundle 生成完整 GLD HTML 决策卡，但仍不是实时 Plugin、真实行情、可交易决策或 broker 连接。

## 固定研究资产

- [`GLD Indicator Management Research Asset`](docs/GLD_INDICATOR_MANAGEMENT_RESEARCH_ASSET.md)：持续保存持仓管理状态机、ATR/RSI/MACD/DI-ADX 判断卡、诱空恢复设计、待验证参数和 owner decision log。该资产固定为 `RESEARCH_ONLY`；其内容不会自动进入权威 `f`。
- [`GLD Entry Policy Research Asset F0`](docs/GLD_ENTRY_POLICY_RESEARCH_ASSET_F0.md)：固定保存Entry Gate、Delta、风险、Preference、Hard Stop候选和Evidence V2预注册方法。它不包含Active theta，也不会由synthetic结果自动晋升。

## Trading Platform｜GLD Entry Decision f F0（研究验证版）

隔离的 `gld_entry_decision_f0` 包把已验证Entry/Technical facts、trial policy及LC0/BCS0独立evidence归约为唯一、确定性的Entry候选结果。每份结果固定包含八阶段Decision Trace：输入资格、Entry Gate、LC0、BCS0、数量、Preference、退出政策绑定和最终结论。一个结构完整合格时输出单方案；两个都合格时输出确定首选及解释性备选。旧备选不得自动替代首选。

已签入的正式Owner验收产物位于 `artifacts/gld-entry-decision-f0-owner-acceptance-v1/`，可直接打开其 `index.html`查看。如需在全新checkout中重新验证生成能力，使用一个尚不存在的本地Owner-run目录：

```bash
PYTHONPATH=src python3 tools/run_gld_entry_decision_f0.py \
  --demo-acceptance \
  --output artifacts/gld-entry-decision-f0-owner-run-local
```

打开 `artifacts/gld-entry-decision-f0-owner-acceptance-v1/index.html`，可按10个业务问题验收停止位置、Gate分组、独立结构、硬cap、Preference、退出政策、可解释性和重复性。所有结果固定为 `RESEARCH_ONLY / NO_DECISION_EFFECT / actionable=false / broker_order_count=0`。R1只证明本地synthetic算法可运行、可解释、可重复并会失败关闭；不证明参数最优、策略盈利、真实数据资格、Active theta或交易能力。人类可读规范见 [`GLD Entry Decision f F0 Spec`](docs/GLD_ENTRY_DECISION_F0_SPEC.md)。

Jake已于2026-09-01明确回复“R1 1–10全部认同”。R1人工状态由独立的[`Owner Acceptance Receipt`](2026-09-01-2157-gld-entry-decision-f0-owner-acceptance-receipt-v0.1.md)记录为`OWNER_ACCEPTED_COMPLETE`。自动生成的acceptance manifest继续保持验收前的`OWNER_ACCEPTANCE_PENDING`，不能被程序改写成Owner签字；Owner回执只接受当前本地synthetic行为，不产生Active theta，也不扩大到真实数据、broker、部署或交易。

## Trading Platform｜GLD 持仓管理 F0（研究验证版）

独立的 `gld_management_research` 包从 220 个完整 synthetic GLD 日线事实本地计算 SMA50、ATR14、RSI14、MACD 12/26/9、DI/ADX 与 volume median，归约为 Structure、Trend、Momentum 和四套预注册 policy。Primary F0 使用 50/30/20，并把 Final Score、两日确认、仓位层级与次日 10:45 ET action facts 组合成非执行性的 `ADD / HOLD / REDUCE / EXIT_DUE / NO_DECISION` 研究说明。它不导入现有 `gld_simulation`、`gld_data_contracts` 或 `gld_research_core` runtime，也不修改权威 `f`、DecisionResult 或现有卡片。

已签入的正式Owner验收产物位于 `artifacts/gld-management-score-f0-owner-acceptance-v1/`，可直接打开其简版Owner首页。如需在全新checkout中重新验证生成能力，使用一个尚不存在的本地Owner-run目录：

```bash
PYTHONPATH=src python3 tools/run_gld_management_score_f0.py \
  --demo-acceptance \
  --output artifacts/gld-management-score-f0-owner-run-local
```

打开 `artifacts/gld-management-score-f0-owner-acceptance-v1/management-score-f0-acceptance/index.html`，即可用六个全中文业务问题验收中性保持、转弱确认、恢复加回、终局安全、数据质量和BCS完整组合。首页不显示hash、JSON或内部reason code；完整14场景、input/formula/policy/result hashes及JSON/Card链接保留在同目录的`engineering-evidence.html`。自动生成的工程证据固定保持`OWNER_ACCEPTANCE_PENDING`，不能替Jake签字；Jake已于2026-09-01明确批准六项简版验收，人工状态由[`Owner Acceptance Receipt`](2026-09-01-1229-gld-management-research-f0-owner-acceptance-receipt-v0.1.md)、固定研究资产和Linear Milestone共同记录为`OWNER_ACCEPTED_COMPLETE`。

单场景模式接受两个严格 canonical JSON 文件，并生成输入副本、`management-score-f0.json` 与 `management-score-f0.html`：

```bash
PYTHONPATH=src python3 tools/run_gld_management_score_f0.py \
  --observation <management-observation-f0.json> \
  --action-snapshot <management-action-snapshot-f0.json> \
  --output <new-output-directory>
```

所有F0产物固定为 `RESEARCH_ONLY / NO_DECISION_EFFECT / actionable=false / broker_order_count=0`。阈值、权重与管理效果仍是 `TRIAL_CANDIDATE_NOT_VERIFIED`，不能解释为真实策略收益或交易指令。

F0日历使用内容hash固定的 `XNYS_PINNED_RESEARCH_F0_V1`：本地验证220个连续session、常规节假日、已登记临时闭市、13:00/16:00 ET正式收盘、DST及次session 10:45 ET。它的状态仍是 `RESEARCH_ONLY_NOT_PROVIDER_QUALIFIED`，不代表真实provider日历资格。终局hard-stop/expiry/invalidation使用独立event fact并把同一episode锁存为 `EXIT_DUE_LATCHED`；次日评分不能重新打开该episode。

两日确认状态不接受裸counter：`derive_management_state_transition_f0(...)`从上一份完整canonical score result与action result确定性生成下一份state，绑定episode、日期、formula/policy、两个result hash和terminal latch；episode首次使用只能在固定`management_start_date`生成零streak genesis。恢复层级只读取同一份sealed action policy。Breakout line只生成独立`NO_ACTION_AUTHORITY` episode diagnostic；改变它不会改变任何dimension、四套policy数值或action选择。

这些lineage hash是无密钥SHA-256完整性校验，只能证明synthetic receipt内部自洽，不能证明来源身份、可信存储或防回滚。真实账户使用前仍需受信state store或签名/attestation。当前本地artifact发布与32 MiB JSON读取也只按synthetic验收边界设计，不宣称production-grade并发对抗或流式资源防护。

## Entry Data Contract → Required Technical Facts V1

新的 `gld_data_contracts` 包把数据验收与旧 decision pipeline 隔离：

```text
legacy 11-document synthetic bundle
→ EntryFactBundleV1
→ RequiredDailyTechnicalFactsV1
```

它提供 `validate_entry_bundle(...)`、`derive_required_technical_facts(...)`、`materialize_bcs_pair_facts(...)` 和 `validate_carrier_evidence(...)`。Entry、catalog 与 canonical output 是 closed schema，禁止 JSON float、未知字段及预填 SMA/IV/Delta/winner/quantity/preference/DecisionResult；Call universe 必须完整且长度可变，64 不是 universe 规则。每项 `source_fact_hash` 只绑定 catalog 声明且公式实际选中的字段和值，完整 raw lineage 则由独立的 `entry_bundle_sha256` 保留。BCS pairs 使用线性 factored relation 与一份共享 component store 保存所有兼容组合，兼容身份包含 `deliverable_shares`；调用者显式给出 long/short IDs 后，decoder 扫描 factored group（最坏 O(n)）还原该 pair 的 Delta、width、debit、stress、cap、max-loss 与 liquidity，不做选择或 preference，也不预物化 O(n²) pair。输入与输出的 100,000-node 资源边界独立显式，超限拒绝而不截断。LC0 与 BCS0 evidence 必须独立，旧共享 Kelly receipt 不会被 adapter 升格。

本地验收命令：

```bash
PYTHONPATH=src python3 tools/validate_gld_data_contracts.py \
  --legacy-bundle fixtures/gld_simulation/v1/pass \
  --demo-failures \
  --output-dir artifacts/gld-data-contracts-v1-run
```

该验收 CLI 固定使用本地 reference engine，不接受 native manifest 或可注入模型 callback；完整 64-contract sample 因此会明显较慢。相同 Entry、pinned catalog、模型版本和 runtime fingerprint 应产生相同 canonical bytes。模型入口及每次 compute 前后还会读取 live C `fegetround()`；V1 仅在已审核的 macOS arm64/x86_64 ABI 上接受 `FE_TONEAREST`，其他模式、读取失败或未审核平台固定以 `TECHNICAL_NUMERIC_ENVIRONMENT_UNSUPPORTED` 拒绝，不猜测 `FE_*` 宏值。reference compute/verify callable 在 import 时捕获，public global 漂移或 sealed runtime 使用替换 callable 会在 CRR 前以 `TECHNICAL_MODEL_RUNTIME_IDENTITY_MISMATCH` 拒绝，阻止 wrapper 在内部瞬时改 rounding 后恢复来绕过采样。相同检查点还要求 import-captured `sys.getprofile`/`sys.gettrace` 身份未漂移且 live 值为 null；active profiler/trace 会以 `TECHNICAL_MODEL_RUNTIME_HOOK_UNSUPPORTED` 拒绝，不能用 call/return callback 瞬时改变 rounding。顶层保存一份 canonical numeric-environment receipt/hash，每份 convergence 保留 core run hash，并以 core + 环境 hash 生成可重算的 composite `run_sha256`。合同、catalog、hash 投影和输出只使用整数；当前 CRR reference engine 内部仍使用 Python IEEE/libm 运算，因此这些 runtime 与舍入门是显式限制，并不等于已经实现无 binary-float 内核。direct Entry 文件使用同一 fd 有界读取并复核 read 前后 inode/size/mtime/ctime；legacy adapter 使用 dirfd、nofollow/nonblock、有界读取、前后闭集扫描和私有 regular-file snapshot，未登记目录、深度或 4,096 tree-node 上限越界均拒绝。CLI 只在不可见 staging 目录名中使用随机性，并通过 no-replace 原子 rename 发布；随机值不进入 Entry、technical facts 或任何 canonical hash。数据字典见 `docs/GLD_RAW_INPUT_DATA_DICTIONARY_V1.md`，机器 feature catalog 见 `docs/GLD_TECHNICAL_FEATURE_CATALOG_V1.json`。

这条链只产生 raw/technical facts，不运行 Gate/carrier PASS/FAIL、数量或 preference，也没有修改现有 `f`、DecisionResult、HTML 和交易状态。旧样本固定是 `SYNTHETIC_ONLY / STRUCTURALLY_VALID_SYNTHETIC`；synthetic carrier Kelly 固定为 0。V1 尚未实现每次 provider observation 与 domain content hash 的 attestation，因此所有 `PRODUCTION_CANDIDATE` 都 fail closed 为 `DATA_NOT_QUALIFIED`，不能证明真实数据或策略有效。

## Raw Simulation → GLD Decision Card

`fixtures/gld_simulation/v1/` 提供三个独立 raw 场景。输入中没有预制 `signal_state`、LC0/BCS winner、quantity 或 DecisionResult；程序依次完成 Gate A、exact-64 CRR、LC0、BCS、Half Kelly 风险容量与数量计算。

- `pass`：完整显示 LC0 与 BCS0，终态为 `SIMULATED_OWNER_SELECTION_REQUIRED`，由 Jake 人工选择。
- `no_action`：事实完整但 Gate A 不满足，终态为 `SIMULATED_NO_ACTION`。
- `no_decision`：缺少必要的 settled cash，终态为 `SIMULATED_NO_DECISION`，不暴露合约或数量。

三种终态都固定为 `SIMULATION_ONLY`、`actionable=false`、`broker_order_count=0`。HTML 只渲染 canonical DecisionResult，不重新运行或改写策略事实。

父进程会从 raw bundle 重放 Gate A、64 个 CRR input binding、LC0/BCS 选择、成本、Half-Kelly sizing、H20 与退出日期，并重算 terminal/input/provenance hashes。CRR 数值本身不会在父进程重复计算：它由 5 秒 supervisor 隔离的 child 运行并通过 `verify_batch` 后 attest；卡片会明确显示这条信任边界。父进程只静态验证 native manifest、binary bytes 与 combined backend identity，不 import 或执行 native extension。5 秒硬截止覆盖最终 artifact staging；截止后不会原子发布 PASS，而会改为 fail-closed `SIMULATED_NO_DECISION`。

本地运行时显式传入已经构建并 pin 的 native manifest 与其中的 backend evidence hash：

```bash
PYTHONPATH=src python3 tools/run_gld_simulation.py \
  --bundle fixtures/gld_simulation/v1/pass \
  --native-manifest .build/crr_native/<build-id>/manifest_v1.json \
  --expected-backend-evidence-sha256 <sha256> \
  --output artifacts/gld-simulation-pass
```

成功后固定生成 `decision-result.json`、`decision-receipt.json` 与 `decision-card.html`。输出目录必须不存在；流程不会覆盖已有产物。

## 已实现

- `SignalSnapshotV1`：从交易日确定性派生 10:45 ET cutoff，并严格绑定此前事实、规则、calendar、phase 与 completeness receipts。
- `OptionQuoteSnapshotV1`：在 `[10:45,10:46)` 中按 append-only ordinal 选择第一张数据质量合格快照，不等待更优价格，也不接收窗口前遗留报价；freshness/skew 固定为 5 秒/1 秒并绑定 policy hash，调用方不能放宽。
- `GLD_CALL_DELTA_CRR_AM_V1`：固定 American CRR、coarse/fine 双网格、32 次 IV bisection 和 ppm half-even rounding；模型 receipt 绑定合同、配置、源码、canonical encoder 与 Python runtime。
- 单一 `1:1` debit Bull Call Spread challenger：long leg 等于 LC0 binding；short target Delta 0.25，band `[0.20,0.30]`，唯一 tie-break。
- Ask/Bid 双腿 entry/exit、每条腿每个 side 的费用与 adverse tick stress。
- BCS no-roll lifecycle、episode-bound 管理快照、exit latch、typed Snapshot B reconciliation receipt 和 blocking notification payload。
- `DecisionArtifactV1`：同时绑定 LC0 与 BCS0。当前正式 LC0 evaluator/selector authority 尚未实现，因此 builder 固定把 LC0 记为 `NO_DECISION / LC0_AUTHORITY_NOT_IMPLEMENTED`，整个 artifact 也保持 `NO_DECISION`；不会从单个 BCS candidate 静默晋升。
- 所有研究候选均为 `actionable=False`、`broker_order_count=0`。

Public lifecycle 刻意不产生 `CLOSING` 或 `CLOSED`。在真实 broker reconciliation authority 尚未实现时，即使 synthetic Snapshot B 的结构完整，也只能保持 `RECONCILIATION_BLOCKED`，不能解除交易阻断。

## 本地验证

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

这只证明 synthetic inputs 下的结构、数学、失败语义和重复运行一致；不证明策略有收益，也不证明真实行情、账户、费用或 broker 能力已资格。

CRR 结果只有由本进程的计算引擎实际产出时才可进入 BCS selector；反序列化或直接构造的结果必须重新计算。这个 process-local seal 是受信任本地进程内的 provenance guard，不是针对任意同进程代码执行、`ctypes` 或 debugger 的安全沙箱，也不是市场数据或券商权威证明。

## 包边界

- `gld_normalizer`：旧有 underlying fixture decoder、causality 与 calendar/phase fail-closed 结构。
- `gld_research_core.facts`：不可变信号/期权快照和稳定 hash。
- `gld_research_core.crr_delta`：本地 American CRR reference engine。
- `gld_research_core.bcs`：BCS 选腿、成本、生命周期和研究终局。

代码中没有 `from_ibkr`、`from_robinhood`、订单创建/提交或通知发送接口。

## 进入真实日常链路前仍缺

- IBKR quote-source qualification：完整 chain、同步 BBO/size、identity、entitlement/data type、时钟、费用和 combo capability。
- Gate A active 规则与 LC0 selector 的正式实现/receipt。
- PIT rate、GLD expense/distribution、borrow 和 effective-dated fee authority。
- 历史 OPRA 等价 BBO 数据、精确下载成本、Owner 授权、outcome access 与 sealed validation。
- Snapshot B 的真实 broker ledger adapter、来源资格和终局清算证明；当前 public lifecycle 不会产生 `CLOSING/CLOSED`。
- 进一步性能优化：当前节点只冻结正确性与 5 秒 fail-closed 边界；完整功能链完成后再做性能验收，且不得通过降低 CRR 步数、缩小必要候选集或改用 broker Delta 暗改模型。

在以上边界关闭前，真实路径必须保持 `NO_DECISION`。
