# GLD Long Call Wave 1 Data Qualification Report

Created: 2026-08-28 07:19 Asia/Shanghai

状态：`WAVE1_COMPLETE / PARTIAL_DATA_FOUND / DATA_NOT_QUALIFIED / NO_BACKTEST_RUN`

## 1. 本轮只回答什么

本轮只判断旧项目的本地数据是否具备研究 `1A + 2B` 的字段、时间、来源和隔离条件。

本轮没有：

- 读取或比较任何 P&L/outcome；
- 运行回测、策略评分或参数选择；
- 连接 Databento 或 broker；
- 读取 `.env`、credential、token、账户、持仓或订单数据。

## 2. 总终局

| 研究面 | 终局 | 含义 |
|---|---|---|
| 1A underlying | `PAYLOAD_FOUND / NOT_YET_QUALIFIED` | 找到两段 GLD 逐笔成交原始数据，可进入清洗与资格设计，但不能进入回测。 |
| 2B option path | `INSUFFICIENT_EVIDENCE` | 六个旧 artifact 的身份和顶层 hash 一致，但不足以支持若干周持有、signal exit 或 roll-up。 |
| Provenance/license | `NOT_CLOSED` | 来源 capability 和部分 receipt 存在，但 retained-use、governing terms 和完整 provenance 未闭合。 |
| Sealed | `NO_PROVABLY_UNSEEN_INTERVAL` | 旧数据和旧事件统一只作 Development；当前没有可证明未见的最终验证区间。 |

因此，Wave 1 已完成，但 Gate A/B 的收益研究仍然不允许启动。

## 3. 1A underlying 数据证据

### 3.1 历史主体段

- 文件：[0001-equs-mini-gld-trades-2023-03-28-through-2025-01-14.csv.zst](</Users/jake/Desktop/GLD_MD_Project_2026-08-08/data/private/f3-momentum-recovery-v0.1/batches/h0-historical-underlying-v0.1/raw/0001-equs-mini-gld-trades-2023-03-28-through-2025-01-14.csv.zst>)
- Bytes：`57,390,794`
- SHA-256：`9ee80806c4e5d212faf19a49251f50d55b8351ed13f5846bdff0732b64827288`
- Provider/request：Databento `EQUS.MINI / trades / GLD`
- 实际 `ts_event` 覆盖：`2023-03-28T11:38:35.638704515Z` 至 `2025-01-14T23:50:04.107396607Z`
- Rows：`2,099,775`
- 主要字段：`ts_recv, ts_event, instrument_id, action, side, price, size, flags, sequence`
- 完整性摘要：错误字段数行 0；空字段行 0；相邻完全重复行 0。

### 3.2 延伸段

- 文件：[sample-extension-gld-trades.dbn.zst](</Users/jake/Desktop/GLD_MD_Project_2026-08-08/data/private/f3-momentum-recovery-v0.1/batches/plugin-mvp-v0.1/sample-extension-gld-trades.dbn.zst>)
- Bytes：`38,282,792`
- SHA-256：`edf34c3bd0ac71c26fc92c128156feb966930fff385aa30a8519404e382b5d7a`
- Provider/request：Databento `EQUS.MINI / trades / GLD`
- 实际 `ts_event` 覆盖：`2025-01-15T12:38:04.009092478Z` 至 `2026-08-14T23:51:36.671499233Z`
- Rows：`2,528,516`
- PIT 字段：`ts_event, ts_recv, sequence`
- 完整性摘要：truncated/bad records 0；相邻完全重复 binary records 714。这 714 条是合法 retransmission 还是需要去重，尚未冻结。

### 3.3 对 1A 的含义

两段逐笔 trades 理论上可确定性派生：

- 前一交易日的 raw OHLCV，用于慢趋势资格；
- post-open 固定 cutoff 之前的 price/volume path，用于快确认。

但在以下项目冻结前，仍不能运行 Entry 研究：

1. 权威 XNYS 交易日历、holiday 和 half-day 绑定；
2. corporate action/distribution 与 raw/adjusted 口径；
3. CSV 与 DBN 两种容器的统一 normalization；
4. session completeness 检查；
5. 09:30 auction 与 continuous trades 的确定性区分；
6. duplicate/retransmission 处理规则；
7. exact post-open cutoff；
8. license 和 retained-use 确认。

## 4. 2B option 数据证据

六个 hash-bound artifacts 的实际 SHA-256 与旧 manifest 声明一致：

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| [35-event cohort](</Users/jake/Desktop/GLD_MD_Project_2026-08-08/data/private/f3-momentum-recovery-v0.1/batches/h0-momentum-cohort-replay-v0.1/artifacts/momentum-default-cohort-replay-v0.1.json>) | 1,385 | `67b9d3dd2c98f9f9c26d11b16f53315989b889d0df4bdffdd6f50863cc8fa6ca` |
| [Selected Calls](</Users/jake/Desktop/GLD_MD_Project_2026-08-08/data/private/f3-momentum-recovery-v0.1/batches/momentum-phase1-selected-legs-v0.3/artifacts/phase1-selected-legs.json>) | 32,540 | `63855c51220fc9a467bb9eb4e04e4721e01be4db6de64929f2e4440fddf0f6a2` |
| [Definition package](</Users/jake/Desktop/GLD_MD_Project_2026-08-08/data/private/f3-momentum-recovery-v0.1/batches/h1-option-phase1-v0.1/package.json>) | 198,068 | `d93316e18e153ccfb349d8b37000f1a4d982812ff3c5c05aca122f3f09ba3624` |
| [Definition manifest](</Users/jake/Desktop/GLD_MD_Project_2026-08-08/data/private/f3-momentum-recovery-v0.1/batches/h1-option-phase1-v0.1/manifest.json>) | 3,655 | `611e10155473bc4d05e4754b4e60405b1f063907c53253c1c7d149e02d9a0c18` |
| [Selected-call path package](</Users/jake/Desktop/GLD_MD_Project_2026-08-08/data/private/f3-momentum-recovery-v0.1/batches/h2-option-path-identity-safe-v0.2/package.json>) | 1,048,902 | `f542e0c87b31f64f9ac0e4fe1b9fe6f6e3528bf5d76a172897c7dfb436be1e1` |
| [Entry CMBP package](</Users/jake/Desktop/GLD_MD_Project_2026-08-08/data/private/f3-momentum-recovery-v0.1/batches/momentum-entry-cmbp1-acquisition-v0.1/package.json>) | 64,258 | `82d4e31a417e44d31c883dd59a1dd4f293ea67fb52c10c8896a82936b9c2f877` |

它们可证明：

- 35 个旧事件日，范围 `2023-10-27..2025-01-08`；
- 35 行、32 个 distinct selected Call identities；
- 旧 selected Calls 的 DTE 只在 `30..36`；
- 定义、统计、CBBO-1m、trades、CMBP-1 字段存在；
- 部分 selected-call 路径数据存在。

它们不能证明：

- 新 Gate A 生成的完整 PIT 机会集；
- 因果 Delta/Greeks 或可复算它们的完整输入版本；
- 若干周内每个 lifecycle checkpoint 的完整路径；
- Gate A invalidation 时的 executable exit bid；
- roll trigger 时的旧 Call bid + 同到期更高 strike 新 Call ask + 第二份 fresh snapshot；
- effective-dated fees/commission 和未成交模型。

### 4.1 对 `B_MEDIUM_SWING` 的直接影响

旧 selected Calls 的 DTE 为 30–36 个日历日。如果新策略的 maximum hold 是约 4 个交易周，这批旧合约到 maximum-hold 时将接近到期，无法同时保留有意义的 expiry safety buffer。

这是时间结构不匹配，不是收益结论。因此旧 30–36 DTE 合约不得直接用来验证 2B。

## 5. 污染、许可和 Sealed

- 35 个事件日、35 个旧 selected Calls 以及两段 underlying 数据都已服务过旧开发。
- 旧 74 个事件和任何已读取过 outcome 的区间统一归入 Development。
- 当前没有 hash-bound 污染账本将 `payload hash → 精确日期 → 谁/何时读取 outcome → 允许阶段` 连起来。
- 文件名中的 `sealed`、设计预注册或 metadata 日期不能自动证明未见。
- 旧回执仍未闭合 governing terms、镜像/保留/删除权和账户 entitlement。

当前唯一安全口径是：全部旧数据只作 Development；最终 Sealed 要么等待新 forward 数据，要么先证明一个未见历史区间，并在读取结果前冻结整套规则和访问协议。

## 6. 下一个 Owner Decision Card

在定义最小补数范围前，必须先冻结 medium-swing 的 maximum hold 和 expiry safety。否则无法判断期权路径要买多长、每天要取哪些合约。

### 3A（推荐）｜四个交易周

- maximum hold：20 个交易 session；
- 进场时选择“在 maximum-hold session 结束时仍至少剩余 30 个日历 DTE”的最早 expiry；
- Gate A invalidation 可以提前触发退出；20 sessions 是上限，不是强制持有。

选择理由：它把“若干周”明确为四个交易周，同时避免研究合约在 maximum hold 附近已进入最后 30 个日历日。这是风险边界，不是对最优收益的声称。

### 3B｜三个交易周

- maximum hold：15 个交易 session；
- maximum-hold session 结束时至少剩余 21 个日历 DTE；
- 数据路径较短，但更接近你刚才认为太集中的短周期。

### 3C｜六个交易周

- maximum hold：30 个交易 session；
- maximum-hold session 结束时至少剩余 30 个日历 DTE；
- 需要更长的合约路径、更长入场 DTE 和更少的独立事件。

这一决定只冻结研究边界，不会启动下载、付费、回测或交易。

## 7. 决定后的最小下一步

一旦 3A/3B/3C 冻结，下一步只是完成两份可审计规格：

1. `Underlying Normalization Contract`：日历、session、auction、adjustment、duplicate 和两种容器统一语义；
2. `Minimum Option Data Gap Contract`：完整 PIT 机会集、因果 Delta 或可复算输入、持有期内同到期链双边报价、fees 和 fill assumptions。

只有这两份规格完成后，才能准确计算是否需要 Databento 补数以及最小补数量。

