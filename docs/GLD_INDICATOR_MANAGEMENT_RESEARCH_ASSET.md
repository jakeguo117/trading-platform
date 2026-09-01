# GLD Indicator Management Research Asset

状态：`RESEARCH_ONLY / LOCAL_F0_IMPLEMENTED_SYNTHETIC / OWNER_ACCEPTED_COMPLETE / NO_GLD_OUTCOME_VALIDATION`

这是一份固定路径、持续修订的研究资产。它保存 GLD 持仓管理指标的共同语言、已接受的产品方向、尚待验证的算法候选和原始资料索引，供后续设计、研究和复审复用。

它不是权威决策函数 `f`，不改变 `EntryFactBundleV1`、`RequiredDailyTechnicalFactsV1`、carrier PASS/FAIL、quantity、preference、`DecisionResult` 或交易状态。

## 1. Metadata

| field | value |
|---|---|
| `asset_id` | `GLD_INDICATOR_MANAGEMENT_RESEARCH_ASSET` |
| `asset_schema_version` | `V1` |
| `asset_revision` | `0.7` |
| `last_updated` | `2026-09-01 Asia/Shanghai` |
| `scope` | `GLD / LC0_AND_BCS0_POSITION_MANAGEMENT_RESEARCH` |
| `asset_owner_status` | `OWNER_ACCEPTED` — 固定保存并复用这份资产 |
| `formula_source_support_status` | `FORMULA_SOURCE_REGISTERED` |
| `research_method_source_support_status` | `RESEARCH_METHOD_SOURCE_REGISTERED` |
| `gld_outcome_evidence_status` | `NOT_RUN` |
| `authority_status` | `RESEARCH_ONLY` |
| `implementation_status` | `LOCAL_RESEARCH_F0_IMPLEMENTED_SYNTHETIC` |
| `local_verification_status` | `556_TESTS_PASS / INDEPENDENT_REVIEW_APPROVE / OWNER_ACCEPTED_COMPLETE` |
| `f0_owner_acceptance_receipt` | [`2026-09-01-1229-gld-management-research-f0-owner-acceptance-receipt-v0.1.md`](../2026-09-01-1229-gld-management-research-f0-owner-acceptance-receipt-v0.1.md) |
| `f0_owner_acceptance_receipt_sha256` | `b3d6d54d6e50b3f8460aed35758d04ca971ce0350da2a12aa1545f7aa2c9c5fb` |
| `outcome_access` | `NONE` |
| `broker_authority` | `NONE` |
| `source_research_task` | `GLD技术指标与诱空防护研究` |
| `source_research_task_id` | `01a055b3-283f-7713-9201-b3756229bb1d` |

### Parent contracts

| artifact | role | SHA-256 at asset revision 0.1 |
|---|---|---|
| [`2026-08-28-1515-gld-research-contract-v0.2.md`](../2026-08-28-1515-gld-research-contract-v0.2.md) | carrier、快照、研究与权限边界 | `3940c27b4aec3585b9aa904a96f2118178589fafdcee46412441431ff9fcb8fc` |
| [`GLD_ADDITIONAL_DATA_CATALOGS_V1.md`](GLD_ADDITIONAL_DATA_CATALOGS_V1.md) | Research/Management facts 的当前 catalog-only 边界 | `ff578db497e382cb50fcc5127e6497b3361865158d2bf529040f1fb4fc5d534e` |
| [`GLD_RAW_INPUT_DATA_DICTIONARY_V1.md`](GLD_RAW_INPUT_DATA_DICTIONARY_V1.md) | raw facts 的来源、时间、单位与缺失语义 | `245d85ce7f6a1d35632e795c56de2f6dc33c631b413ee3f60bfa0e717922bad1` |
| [`GLD_TECHNICAL_FEATURE_CATALOG_V1.json`](GLD_TECHNICAL_FEATURE_CATALOG_V1.json) | 当前 authoritative technical facts V1 | `64a754650da2af1c24d71ffc7ffbcd72629ed433acb96ffdee57c512b9faa5e0` |

父合同 hash 只是 revision 0.1 的来源登记；父文件变化不会自动升级本资产，也不会自动改变任何决策逻辑。

## 2. Status semantics

每条决定或 candidate 分别记录四种状态，禁止互相替代：

| axis | allowed values | meaning |
|---|---|---|
| `owner_status` | `OWNER_ACCEPTED \| OPEN \| DEFERRED` | Jake 是否接受这个产品方向或定义 |
| `source_support_status` | `UNSOURCED \| FORMULA_SOURCE_REGISTERED \| RESEARCH_METHOD_SOURCE_REGISTERED` | 是否登记了公式实现或研究方法来源；不表示 GLD outcome 有效 |
| `evidence_status` | `NOT_RUN \| DEVELOPMENT_PASS \| WALK_FORWARD_PASS \| SEALED_PASS \| REJECTED` | GLD after-cost outcome evidence 到了哪一层 |
| `authority_status` | `RESEARCH_ONLY \| ELIGIBLE_FOR_PROMOTION \| AUTHORITATIVE_F_VERSION` | 是否有资格影响权威 `f` |

`OWNER_ACCEPTED` 只表示接受明确写出的 scope。例如“接受研究 ATR”不表示接受任何 ATR 阈值。只有公式、数据、trial、walk-forward、sealed/OOS、成本与失败语义全部冻结并通过，candidate 才可能申请 `ELIGIBLE_FOR_PROMOTION`；晋升仍需单独的 owner 决定和版本变更。

## 3. Current accepted decisions

| decision | owner status | accepted scope | not accepted / not proven |
|---|---|---|---|
| `B_PLUS_3_MANAGEMENT_ARCHITECTURE` | `OWNER_ACCEPTED` | 使用可恢复的状态机，并保留独立硬止损通道；弱信号不直接锁死退出 | hard-stop exact trigger 仍在 F0 外部 |
| `MANAGEMENT_CLOCKS_V1_DIRECTION` | `OWNER_ACCEPTED` | XNYS 正式收盘且日线完整后计算状态；下一 XNYS session 10:45 ET 用 fresh BBO 执行动作；硬止损独立盘中/event-driven 监控 | synthetic精确时钟已实现；真实provider资格和未来未登记临时闭市仍未验收 |
| `FALSE_BREAKDOWN_RECOVERABILITY` | `OWNER_ACCEPTED` | 单一转弱信号先等待第二个完整收盘；防御确认后降层，强势恢复也需两个完整收盘 | 这些是未验证的 F0 candidate，不代表 outcome 有效 |
| `COMPLETE_STRUCTURE_UNIT_ACTION` | `OWNER_ACCEPTED` | LC0 只能按完整合约单位调整；BCS0 只能按完整 `1:1` 组合单位调整；score cap 使用 original units 的 `ceil(75%)`/`ceil(50%)` | 外部risk cap与独立退出仍可为0 |
| `REPLACEMENT_IS_NEW_GENERATION` | `OWNER_ACCEPTED` | 换便宜 Call 或 LC0→BCS 不是原持仓的“减仓”；若未来研究，必须先处理旧仓，再以 fresh facts 重跑新 entry `f` | 第一代管理策略不自动 replacement |
| `INDICATOR_ROLE_SEPARATION` | `OWNER_ACCEPTED` | Structure、Trend、Momentum 先各自绑定方向与强度；RSI 与 MACD 在同一个 Momentum dimension 内等权合成，不形成两张票 | 权重与收益有效性仍未获得 outcome 证明 |
| `RESEARCH_SYNC_CONTRACT_V1` | `OWNER_ACCEPTED` | Jake 明确接受、确定或冻结研究结论后，按同一 Asset revision/hash 同步到 Linear 与 Obsidian 项目摘要 | 讨论草稿不同步；不引入 Asana；不建设后台同步服务；镜像状态不构成 `f` 晋升或实施授权 |
| `MANAGEMENT_SCORE_F0` | `OWNER_ACCEPTED` | Primary 使用 Structure/Trend/Momentum=`50/30/20`；另保留三套固定 shadow controls；Final Score 映射 HOLD/75%/50%/恢复资格 | 全部阈值标记 `TRIAL_CANDIDATE_NOT_VERIFIED` |
| `RECOVERY_READD_F0` | `OWNER_ACCEPTED` | 两日强势确认后，只允许同 episode、carrier、合约逐层恢复，永不超过 original-approved units | 不是 replacement、roll 或 pyramiding |
| `BREAKOUT_DIAGNOSTIC_ONLY_F0` | `OWNER_ACCEPTED` | breakout 不进入 F0 Final Score，只保留 episode-bound 离线诊断 | 是否未来晋升须新的证据和 owner 决定 |
| `PINNED_RESEARCH_CALENDAR_F0` | `OWNER_ACCEPTED` | synthetic F0以内容hash固定的XNYS research calendar验证连续session、节假日、早收、DST、正式收盘和10:45 ET | `RESEARCH_ONLY_NOT_PROVIDER_QUALIFIED`，不替代真实source qualification |
| `STREAK_LINEAGE_AND_TERMINAL_LATCH_F0` | `OWNER_ACCEPTED` | previous state由纯transition函数从上一份完整canonical score/action results生成，绑定episode、formula/policy、日期、两个result hash和state hash；终局override使用独立event fact并锁存同一episode | 无密钥SHA-256只证明输入内容自洽，不证明来源身份；不实现真实broker exit/fill或跨episode自动恢复 |
| `MANAGEMENT_RESEARCH_F0_OWNER_ACCEPTANCE_V1` | `OWNER_ACCEPTED` | Jake接受简版Owner首页六组业务判断及其对应的本地synthetic F0行为；证据绑定独立追加式回执及revision 0.6资产hash | 不声称逐项审阅14份工程JSON；不证明收益、真实数据资格或权威`f`晋升；不授权部署或交易 |
| `DUAL_PRICE_ANCHOR_HIERARCHY` | `DEFERRED` | 第 7 节保留为 F0 之前的历史候选 | 不影响 F0 Score 或 action |

15:45 ET 不属于第一代权威管理时钟；以后若研究，只能先作为 `WATCH` warning candidate，不能静默增加第二个每日交易时点。

## 4. Human-readable state model

这一节定义“每个判断在问什么”，不冻结阈值。

| state | human question | first-generation action semantics | latch |
|---|---|---|---|
| `STRONG` | 原建仓逻辑仍完整，而且没有持续的反向方向/强度确认吗？ | 保持现有完整结构单位 | no |
| `WATCH` | 出现了值得观察的转弱迹象，但证据是否还不足以行动？ | 保持；记录原因；等待下一完整观察 | no |
| `DEFENSIVE` | 快速锚已经持续受损，但较慢的趋势核心是否仍未失效？ | 只允许减少完整结构单位；数量函数仍待决定 | no |
| `RECOVERY` | `WATCH/DEFENSIVE` 后，Final Score 是否连续两个完整收盘进入 `RESTORE_ELIGIBLE`？ | 次日 10:45 ET 在所有 action facts 合格时，只恢复一个原始仓位层级 | no |
| `INVALIDATED` | 中期趋势核心是否已经按冻结规则确认失效？ | 进入 `EXIT_DUE`，用 fresh executable exit facts 处理完整结构 | yes |
| `HARD_STOP` | 独立、预先冻结的不可容忍损失条件是否触发？ | 立即进入 `EXIT_DUE`；不等待收盘指标投票 | yes |
| `EXPIRY_SAFETY` | 是否进入 H20/last-trading/expiry safety 强制窗口？ | 进入 `EXIT_DUE` | yes |
| `NOT_EVALUABLE` | 所需事实是否缺失、冲突、过期或未资格？ | 不猜测状态、不自动加仓；采用未来合同冻结的 fail-closed 语义 | no automatic upgrade |

优先级候选为：`latched HARD_STOP/EXIT_DUE > EXPIRY_SAFETY > confirmed INVALIDATED > DEFENSIVE > RECOVERY > WATCH > STRONG`。F0只实现本地synthetic research reducer和hypothetical action；没有ManagementFactBundle production authority或权威状态机晋升。

## 5. Indicator decision cards

### 5.1 Price structure — primary anchor

| item | research definition |
|---|---|
| 人能理解的问题 | 价格是否仍守住“为什么当初允许建仓”的结构，以及中期上升趋势是否仍存在？ |
| role | 唯一 primary anchor；除独立 hard stop/expiry 外，指标不能脱离价格结构单独决定退出 |
| required facts | episode-bound entry breakout line、完整 XNYS daily close、SMA50、必要的 entry/rule hashes |
| candidate interpretation | 固定 breakout anchor 提供早期 thesis warning；动态 SMA50 提供慢速 regime invalidation |
| missing/conflict | 任一 episode identity、bar completeness、adjustment semantics 或 anchor binding 不明则 `NOT_EVALUABLE` |
| maximum state alone | breakout 损伤最多进入 `WATCH`；SMA50 是否可单独 invalidation 仍须第三项冻结确认规则 |
| status | F0只使用动态SMA50的ATR标准化距离；dual-anchor内容已转为第7节历史候选 |

### 5.2 ATR14 — distance and volatility scale

| item | research definition |
|---|---|
| 人能理解的问题 | 当前偏离某条价格线的距离，相对于 GLD 最近的正常波动到底大不大？ |
| role | 尺度尺，不判断多空，不参与多数票 |
| candidate formula | `TR_t = max(H_t-L_t, abs(H_t-C_(t-1)), abs(L_t-C_(t-1)))`；14-period Wilder seed/smoothing |
| candidate uses | 把 breakout/SMA 距离标准化；定义 buffer；比较不同波动环境下的同类事件 |
| prohibited use | `ATR rising/falling` 不能单独产生 `STRONG`、`DEFENSIVE` 或 `INVALIDATED` |
| maximum state alone | 不产生方向状态；事实合格时只输出尺度，缺失时使依赖它的规则 `NOT_EVALUABLE` |
| implementation items to freeze | adjustment、warm-up、seed、Wilder smoothing、整数 scale/rounding、zero/overflow、formula/source hashes |
| candidate thresholds | `-0.5 ATR`、`+0.25 ATR` 仅为待测试例，不是已接受参数 |
| owner/evidence/authority | `OWNER_ACCEPTED` research role / `NOT_RUN` / `RESEARCH_ONLY` |

### 5.3 DI14 + ADX14 — direction and trend strength

| item | research definition |
|---|---|
| 人能理解的问题 | 最近的方向力量偏向上还是向下，而且这个方向是在增强还是只是噪音？ |
| role | `+DI/-DI` 提供方向；ADX 提供方向运动的强度，ADX 本身不表示上涨或下跌 |
| candidate formula | Wilder directional movement、TR smoothing、`+DI`、`-DI`、DX 与 ADX14；相等/zero-denominator 语义必须显式冻结 |
| strong contribution | price structure intact、`+DI > -DI`，且上行方向强度不恶化 |
| warning contribution | 一次 DI 交叉或 ADX 与价格方向冲突，最多贡献 `WATCH` |
| defensive contribution | fast anchor 持续受损，同时 `-DI > +DI` 且下行 ADX strength 持续增强 |
| invalidation contribution | slow anchor 已确认失效时，提供 bearish direction/strength confirmation |
| recovery contribution | fast anchor 被持续收复，同时 `+DI > -DI`；是否要求 ADX 回升仍待测试 |
| prohibited use | ADX 高不能被解释为“看多”；单一 DI cross 不能直接退出 |
| maximum state alone | `WATCH` |
| implementation items to freeze | warm-up、seed、ties、smoothing、整数 scale/rounding、persistence、missing/conflict、hashes |
| candidate thresholds | DI 只比较方向、ADX “strengthens” 的 exact delta/window 均未冻结 |
| owner/evidence/authority | `OWNER_ACCEPTED` research role / `NOT_RUN` / `RESEARCH_ONLY` |

### 5.4 RSI14 — momentum challenger A

| item | research definition |
|---|---|
| 人能理解的问题 | 上涨与下跌的近期动量平衡是否正在明显转弱或恢复？ |
| role | 标准化为 `R=(RSI14-50)/50`，与 MACD component 在同一个 Momentum dimension 内等权合成；不是独立投票 |
| candidate formula | 14-period Wilder average gain/loss，`RSI = 100 - 100/(1+RS)`；zero-loss/zero-gain 语义须冻结 |
| warning contribution | RSI 跌破候选弱势区，只能贡献 `WATCH` |
| recovery contribution | RSI 回到候选强势区，可作为 recovery 辅助证据 |
| prohibited use | 超买不等于必须卖；RSI 不能单独触发 `DEFENSIVE` 或 `INVALIDATED` |
| maximum state alone | `WATCH` |
| implementation items to freeze | adjusted close、warm-up、seed、整数 scale/rounding、threshold crossing、persistence、missing/conflict、hashes |
| candidate thresholds | `<45` 为弱势、`>55` 为恢复/强势，仅是 challenger candidate |
| owner/evidence/authority | `OWNER_ACCEPTED` research inclusion / `NOT_RUN` / `RESEARCH_ONLY` |

### 5.5 MACD 12/26/9 — momentum challenger B

| item | research definition |
|---|---|
| 人能理解的问题 | 较快趋势相对较慢趋势是在加速还是减速，而且这种变化是否持续？ |
| role | histogram 以 ATR14 平滑标准化为 `M=hist/(abs(hist)+ATR14)`，与 RSI component 在同一个 Momentum dimension 内等权合成；不是独立投票 |
| candidate formula | `MACD = EMA12 - EMA26`；`signal = EMA9(MACD)`；`histogram = MACD - signal` |
| warning contribution | histogram 持续转负或 bearish cross，只能贡献 `WATCH` |
| recovery contribution | histogram 持续转正，可作为 recovery 辅助证据 |
| prohibited use | 一次 cross 不能直接减仓或退出；不能与 RSI 同时作为两张独立赞成票 |
| maximum state alone | `WATCH` |
| implementation items to freeze | price input、EMA seed、warm-up、first valid index、整数 scale/rounding、cross/tie、persistence、missing/conflict、hashes |
| candidate thresholds | histogram 连续两日负/正仅是 challenger candidate |
| owner/evidence/authority | `OWNER_ACCEPTED` research inclusion / `NOT_RUN` / `RESEARCH_ONLY` |

## 6. Historical cross-indicator reduction candidate — superseded by F0

本节保留 revision 0.1/0.2 的研究来路，但不再是当前 F0 规范。F0 的现行归约固定为：每个指标只在所属 dimension 内形成 signed contribution，之后按预注册权重汇总；breakout 不进入 Score；RSI 与 MACD 合成一个 Momentum dimension；action 使用两日确认和仓位层级。以下旧 `WATCH/dual-anchor/challenger` 表不得被实现读取。

禁止“5 个指标多数票”。同一事实只承担一种职责：价格决定结构，ATR 把距离变成可比较尺度，DI/ADX 负责方向和强度，RSI 或 MACD 只能选择一个作为动量 challenger。

| observable condition | candidate state | candidate action | why it is not a vote |
|---|---|---|---|
| facts missing/conflicting/stale | `NOT_EVALUABLE` | 不加风险；等待合格事实或执行已独立 latch 的退出 | 数据资格先于指标 |
| independent hard stop triggered | `HARD_STOP → EXIT_DUE` | 完整结构退出 | bypass indicators |
| expiry/H20 safety triggered | `EXPIRY_SAFETY → EXIT_DUE` | 完整结构退出 | lifecycle rule |
| both price anchors intact; no persistent bearish DI/ADX confirmation | `STRONG` | hold | structure dominates |
| first breakout re-entry、single DI cross，或一个 challenger warning | `WATCH` | hold and observe | a warning cannot act alone |
| fast anchor persistently below ATR-scaled buffer + bearish DI/ADX; slow anchor intact | `DEFENSIVE` | reduce complete units once | price event plus role-specific confirmation |
| after WATCH/DEFENSIVE, fast anchor persistently recovered + bullish DI direction | `RECOVERY` | historical candidate only | superseded by F0 controlled `RECOVERY_READD` |
| slow anchor persistently invalidated + bearish DI/ADX confirmation | `INVALIDATED → EXIT_DUE` | complete-structure exit; latch | confirmed core thesis failure |

RSI and MACD challenger 的变化只允许改变辅助解释或 candidate 之间的比较，不能在同一 policy 中同时累计成两票。

## 7. Historical owner decision 2 — deferred outside F0

本节选项继续作为历史研究记录。F0 已明确选择动态SMA50的ATR标准化距离作为Structure base；固定breakout只保留无action权限的离线diagnostic。因此这里的Option C不属于当前实现，也不能改变Final Score。

### The two questions

- 固定 entry breakout anchor `P_breakout_H0`：价格是否已经把“当初突破成立”的依据还回去了？它在一个 episode 内保持不变。
- 动态 `SMA50_t`：截至最新完整收盘，中期趋势是否仍然存在？它每天只用完整日线更新。

ATR 只将距离标准化，例如 candidate `fast_z_t = (close_t - P_breakout_H0) / ATR14_t`，不成为第三条方向锚。

### Options

| option | design | advantages | costs / failure modes | current assessment |
|---|---|---|---|---|
| A | 只用固定 breakout anchor | 最贴近原始 entry thesis；反应早；规则简单 | 正常回踩也容易被当成失效，诱空/whipsaw 风险最高；无法描述中期趋势仍完整 | 不推荐作为单一锚 |
| B | 只用动态 SMA50 | 更平滑；较少因小回踩下车；每天自然适应趋势 | 反应慢；忘记原 entry thesis；可能在明显跌回 breakout 后仍持有；强势上涨后可能容忍较大回撤 | 不推荐作为单一锚 |
| C | 分层双锚：breakout 管早期 warning/defensive，SMA50 管 confirmed invalidation | 同时保留 entry 记忆与中期趋势；可以先降风险、保留 exposure，并为假跌破留 recovery 路径 | 状态和测试多于单锚；必须冻结 persistence、ATR buffer、冲突处理与数量函数 | **推荐；`owner_status=OPEN`** |
| D | 再加 rolling high / trailing ATR anchor | 可保护已有浮盈，减少大幅回吐 | 引入路径依赖和大量参数；容易扩大 trial budget；会模糊当前第二项 | 第一代 `DEFERRED` |

### Recommended hierarchy for Option C

```text
fixed breakout damaged once
→ WATCH only

fixed breakout persistently damaged by ATR-scaled distance
+ bearish DI/ADX confirmation
+ SMA50 trend still intact
→ DEFENSIVE; retain some complete-unit exposure

fixed breakout recovered persistently
+ bullish DI direction
→ historical RECOVERY candidate; superseded by F0 two-close controlled re-add

SMA50 persistently invalidated
+ bearish DI/ADX confirmation
→ INVALIDATED; latch EXIT_DUE

independent hard stop or expiry safety
→ bypass hierarchy; latch EXIT_DUE
```

接受 Option C 只冻结“两个锚各自负责什么”，不会顺带接受 `-0.5 ATR`、两天确认、ADX 增强定义、减仓比例或恢复阈值。这些属于后续独立 owner 决定和 trial candidates。

## 8. Carrier action semantics

| carrier | hold | defensive reduction | exit | replacement |
|---|---|---|---|---|
| `LC0` | 保持现有 Call 数量 | 只减少完整 Call 合约数 | 用 fresh exit BBO 处理全部剩余合约 | 不属于管理动作；未来需新 entry generation |
| `BCS0` | 保持现有 `1:1` vertical 数量 | 只减少完整 `1:1` 组合数 | 用 fresh dual-leg/combination facts 处理全部剩余组合 | 禁止只换一条腿；未来需新 entry generation |

F0 的 score-derived 非退出层使用 `ceil(original_approved_units × fraction)`，最低保留一个完整结构单位。若整数结果无法减少单位，则输出 `HOLD / NO_COMPLETE_UNIT_REDUCTION_AVAILABLE`；外部风险 cap 和独立硬退出仍可把目标降为 0。

## 9. F0 pre-registered policy set

所有行均为 offline research；没有 outcome access，也没有任何 PASS。

| candidate id | comparison | frozen weights / factor | owner status | evidence status | authority |
|---|---|---|---|---|---|
| `PRIMARY_F0` | 主可见研究假设 | `500000/300000/200000 ppm`；Structure包含volume factor | `OWNER_ACCEPTED` | `NOT_RUN` | `RESEARCH_ONLY` |
| `CONTROL_EQUAL_WITH_VOLUME` | 权重control | `333334/333333/333333 ppm`；Structure包含volume factor | `OWNER_ACCEPTED` | `NOT_RUN` | `RESEARCH_ONLY` |
| `ABLATION_ROLE_NO_VOLUME` | volume ablation | `500000/300000/200000 ppm`；Structure使用base | `OWNER_ACCEPTED` | `NOT_RUN` | `RESEARCH_ONLY` |
| `ABLATION_EQUAL_NO_VOLUME` | 权重与volume双control | `333334/333333/333333 ppm`；Structure使用base | `OWNER_ACCEPTED` | `NOT_RUN` | `RESEARCH_ONLY` |

四套policy构成全部F0 budget；不得增加运行时政策、动态选择winner或运行参数全排列。只有Primary产生action；三个control只输出score/band。Momentum在全部policy中都固定为 `(R+M)/2`。

### F0 exact score and action contract

```text
StructureBase = (close-SMA50)/(abs(close-SMA50)+ATR14)

if current_volume <= prior_20_normal_session_volume_median:
    volume_quality = 1
else:
    h = (current_volume-reference_volume)/(current_volume+reference_volume)
    alignment = sign(close-prior_close) * sign(close-SMA50)
    volume_quality = 1 + alignment*h

C_structure = clamp(StructureBase*volume_quality, -1, +1)
C_trend = ((+DI14 - -DI14) / (+DI14 + -DI14)) * ADX14/100
R = (RSI14-50)/50
M = MACD_histogram/(abs(MACD_histogram)+ATR14)
C_momentum = (R+M)/2
NetSignal = sum(weight*C_dimension)
```

内部值使用整数ppm及signed round-half-even。Final Score band固定为：`>=400000 RESTORE_ELIGIBLE/100%`、`[-200000,400000) HOLD`、`[-600000,-200000) DEFENSIVE_1/75%`、`<-600000 DEFENSIVE_2/50%`。防御与恢复都需要两个连续完整收盘；缺失/不可评估重置streak。previous state不得提交裸counter或单独hash：它必须由`derive_management_state_transition_f0(...)`从上一份完整canonical score result与action result生成，并绑定同一episode、公式hash、完整score/action policy hash、两个result hash和相邻XNYS session；counter固定在0–2并校验强弱互斥。只有episode固定`management_start_date`允许一次零streak genesis。全部参数是`TRIAL_CANDIDATE_NOT_VERIFIED`。

`RECOVERY_READD`只允许同episode、carrier、合约逐层恢复，最多到original-approved units。Score不能单独产生full exit；hard stop、expiry safety及confirmed invalidation使用独立event-driven terminal fact，设置`EXIT_DUE_LATCHED`；transition一旦看到latch便清零三条streak，此后同一episode在持仓归零后永不被Score重新打开。到期日scheduled snapshot被拒并要求`EXPIRY_SAFETY` terminal fact；DTE缺失在所有Score band全局失败关闭。reconciliation、数据不合格及外部risk cap继续独立覆盖。

Score与action共用一份不可变、运行前seal校验的policy合同hash；它同时绑定四套权重、band阈值、两日确认、75%/50%整数层级、quote event/receive age、receive skew、10:45 ET、override优先级和terminal latch。旧market event不能通过新的receive timestamp伪装成fresh quote。

### Required evaluation outputs

- episode-level after-cost return on original entry debit；
- flow-adjusted maximum drawdown、time under water、tail loss/ES95；
- `quick_recovery_5` 与 economic false-exit rate；
- exposure retention，例如 net-Delta × holding days；
- turnover、execution coverage、spread/slippage/fee impact；
- confirmation-delay cost；
- development、purged walk-forward、sealed/OOS 或 forward split；
- exclusions、missingness、coverage denominator、policy/input/cost hashes。

研究结果必须同时展示收益、回撤、假跌破代价与保留 exposure 的代价；只优化某一个指标不能证明策略可晋升。

## 10. Source register

| source id | primary implementation / peer-reviewed source | supports | does not prove |
|---|---|---|---|
| `SRC_TALIB_ATR` | [TA-Lib ATR source, pinned commit](https://github.com/TA-Lib/ta-lib/blob/12987063d9244f077ead7fbc13a854fe702807a6/ta_codegen/input/atr/atr.c#L19-L137) | 一种可审计的 ATR/Wilder lookback 与计算实现 | GLD 的 ATR buffer 或管理收益有效 |
| `SRC_TALIB_RSI` | [TA-Lib RSI source, pinned commit](https://github.com/TA-Lib/ta-lib/blob/12987063d9244f077ead7fbc13a854fe702807a6/ta_codegen/input/rsi/rsi.c#L20-L300) | 一种可审计的 RSI lookback、seed 与递推实现 | `<45`、`>55` 对 GLD 有效 |
| `SRC_TALIB_MACD` | [TA-Lib MACD source, pinned commit](https://github.com/TA-Lib/ta-lib/blob/12987063d9244f077ead7fbc13a854fe702807a6/ta_codegen/input/macd/macd.c#L23-L260) | MACD fast/slow/signal、seed、lookback 与输出关系的可审计实现 | 12/26/9 或两日 histogram 对本策略最优 |
| `SRC_TALIB_DMI_ADX` | [TA-Lib ADX source, pinned commit](https://github.com/TA-Lib/ta-lib/blob/12987063d9244f077ead7fbc13a854fe702807a6/ta_codegen/input/adx/adx.c#L47-L320)、[+DI definition](https://ta-lib.org/functions/plus_di.html)、[-DI definition](https://ta-lib.org/functions/minus_di.html) | Wilder DMI/ADX 方向、归一化、smoothing、lookback 与初始 ADX average 的可审计实现 | DI/ADX 组合可减少 GLD 假跌破或提高收益 |
| `SRC_BROCK_1992` | [Brock, Lakonishok and LeBaron, Journal of Finance](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1992.tb04681.x) | 经典 moving-average/trading-range 技术规则可以被严格定义并作样本检验 | 该论文直接验证 GLD、期权 carrier 或本候选阈值 |
| `SRC_SULLIVAN_1999` | [Sullivan, Timmermann and White, Journal of Finance](https://doi.org/10.1111/0022-1082.00163) | 技术规则搜索需要量化并校正 data-snooping 偏差 | 任一具体 GLD policy 已通过 OOS，或本资产指定的 split 方法本身充分 |
| `SRC_BAILEY_2017` | [Bailey, Borwein, López de Prado and Zhu, Journal of Computational Finance](https://doi.org/10.21314/JCF.2016.322) | 投资回测存在 overfitting 风险，需要单独评估 IS/OOS 选择过程 | 本资产的 purged walk-forward/sealed 设计已实现或足以证明 GLD policy 有效 |

来源检索日期：`2026-08-31`。Agent 研究任务只作为整合 provenance；上表原始资料才用于支持公式或研究方法。资料的存在不等于 GLD outcome evidence。

## 11. Required data and implementation gates

在任何 candidate 进入可运行 Technical Facts V2 前，至少需要冻结：

- point-in-time、corporate-action-adjusted GLD daily OHLC，完整 XNYS calendar/session 和足够 warm-up；
- 每个 formula 的 exact inputs、seed、lookback、first-valid index、整数 unit/scale、rounding、sorting、missing/conflict semantics；
- episode-bound `P_breakout_H0` 与 rule/source hashes；
- management close facts 与次日 10:45 ET action facts 的严格时间隔离；
- LC0/BCS0 fresh executable exit BBO、费用、spread/slippage、partial/residual-leg 语义；
- deterministic canonical JSON、source fact hash、feature hash、policy hash；
- 相同输入、相同版本产生逐字节相同输出的跨进程测试；
- research-only hash non-effect：本资产或 research bundle 的变化不能改变 V1 authoritative inputs/hashes。

F0只允许本地版本化公式在隔离的`RESEARCH_ONLY`链路中产生研究Score；provider指标、说明文字或UI仍不得影响Entry/Technical Facts V1、Gate、carrier、quantity、preference或DecisionResult。真实数据、outcome与权威晋升继续要求上列gates。

当前lineage使用canonical JSON与无密钥SHA-256发现未同步重算的篡改并校验内部语义，但控制整份synthetic输入的人仍可构造一条自洽的新链。它不是签名、attestation、可信状态存储或anti-rollback证明。若未来接入真实账户，必须先绑定受信上一artifact/state store和来源资格。当前本地发布器还保留两个非阻断hardening项：同用户恶意并发替换目录的TOCTOU窗口，以及32 MiB JSON在完整解析后才执行节点/深度上限；两者不得被解释为production-ready。

## 12. Open, deferred and rejected scope

### Open

- hard-stop 的两条 exact definitions；
- H1–H20 的 exact session indexing 与 expiry priority；
- trial budget、data source、cost、split 和 promotion gates。
- 真实账户lineage的可信状态存储、签名/attestation及anti-rollback；
- production发布的dirfd/no-follow原子目录锚定和解析阶段JSON资源限制。

### Deferred from generation 1

- rolling-high/trailing-ATR 第三锚；
- dual-anchor/breakout 对 F0 Score 的影响；
- 15:45 ET 第二个常规管理动作时点；
- 自动换便宜 Call；
- LC0 与 BCS0 之间的自动转换；
- 任何超出同episode/同合约逐层恢复的自动加仓；
- Bollinger、Gamma/Vega/Theta、IV Rank、skew、term structure、option activity、VIX/GVZ、美元、实际利率、黄金期货结构与 ETF flow 对权威状态的影响。

### Rejected as a design shortcut

- 五个指标简单多数票；
- 一个 RSI/MACD/DI crossing 直接 full exit；
- ADX 高等同于看多；
- ATR 自己决定方向；
- 用 provider indicator 值替代本地版本化公式；
- 把 owner 接受、research PASS 与 authoritative promotion 合并成一个状态。

## 13. Decision log and changelog

| date | revision | change | authority effect |
|---|---|---|---|
| 2026-08-31 | `0.1` | 创建稳定 research asset；保存 B+3、管理时钟、指标职责、状态归约、trial 与第二项双锚选项 | none; remains `RESEARCH_ONLY` |
| 2026-08-31 | `0.2` | 接受 research decision 同步合同：仅在 Jake 明确确认后，以同一 revision/hash 镜像到 Linear 与 Obsidian；Asana 排除在外 | none; remains `RESEARCH_ONLY` |
| 2026-08-31 | `0.3` | 冻结可运行 Research F0：三dimension、四policy、50/30/20 Primary、volume factor、两日确认、75%/50%层级、受限Recovery Re-add、breakout diagnostic-only及可见人工验收合同 | none; remains `RESEARCH_ONLY / NO_DECISION_EFFECT` |
| 2026-09-01 | `0.4` | 加入pinned XNYS research calendar、正式收盘/10:45 ET绑定、streak lineage receipt、完整score/action policy seal、event/receive双freshness、独立terminal override及永久episode latch；扩展可见验收矩阵 | none; remains `RESEARCH_ONLY / NO_DECISION_EFFECT`; owner acceptance pending |
| 2026-09-01 | `0.5` | 完成本地synthetic F0实现与可见验收产物；恢复层级只从sealed policy读取；补周末/早收、override组合和1–5单位恢复矩阵；记录无密钥hash来源边界及两项non-blocking发布/资源hardening；全仓555 tests通过且独立复审APPROVE | none; remains `RESEARCH_ONLY / NO_DECISION_EFFECT`; owner acceptance pending |
| 2026-09-01 | `0.6` | 统一可见命名为“Trading Platform｜GLD 持仓管理验收”；新增六组全中文Owner首页，并把原14场景、hash、JSON和管理卡移到独立工程证据页；所有canonical输入、结果、manifest及原管理卡保持逐字节不变 | none; remains `RESEARCH_ONLY / NO_DECISION_EFFECT`; owner acceptance pending |
| 2026-09-01 | `0.7` | Jake查看六组简版验收后明确表示“我这边可以 approve”；新增追加式Owner回执（SHA-256 `b3d6d54d6e50b3f8460aed35758d04ca971ce0350da2a12aa1545f7aa2c9c5fb`），绑定验收前revision 0.6资产与manifest；自动生成证据继续保持`OWNER_ACCEPTANCE_PENDING` | none; remains `RESEARCH_ONLY / NO_DECISION_EFFECT`; `OWNER_ACCEPTED_COMPLETE`只关闭本地F0人工验收，不证明真实收益或授权交易 |

后续更新规则：保留本路径；递增 `asset_revision`；在本表记录 Jake 的明确决定、候选变化与证据状态。不得通过编辑旧行把 `NOT_RUN` 改写成通过，必须新增可追溯变更记录。

## 14. Accepted mirror synchronization contract

这份 Asset 是 GLD indicator management research 内容的唯一权威来源；Linear 和 Obsidian 都是镜像，不能反向覆盖本文件，也不能据此改变权威 `f`、`DecisionResult` 或交易状态。

### Trigger and order

只有 Jake 明确说“接受”“确定”或“冻结”某个结论时才触发正式同步。普通讨论、建议、候选参数和 agent 推断不得触发。同步顺序固定为：

```text
Research Asset
→ calculate final asset_sha256
→ Linear project/milestone status
→ Obsidian project summary
→ read-back verification
```

### Required mirror identity

每个镜像至少记录：

- `asset_id`；
- `asset_revision`；
- `asset_sha256`；
- `decision_id` 与明确接受范围；
- `not_accepted_or_not_proven`；
- `owner_status`、`evidence_status` 与 `authority_status`；
- 更新时间、当前开放问题和下一项 owner decision。

### Failure and retry semantics

- 任一镜像不可用时，必须报告对应的 `SYNC_PENDING`，不得宣称三处已同步；
- 重试必须先比较 `asset_revision` 和 `asset_sha256`，相同 identity 只补齐缺失镜像，不重复创建记录；
- Linear 只承担项目可视化和 checkpoint，不为每个指标重复创建 issue；
- Obsidian 保存完整项目摘要和本 Asset 的 identity，不复制完整公式、来源表或研究正文；
- Asana 不安装、不接入；除非未来明确用它替代 Linear，或它成为团队强制协作入口；
- 初期由确认结论的同一轮会话执行同步，不运行后台 daemon、cron 或隐式自动化。
