# GLD Long Call Owner Decision OD-03 Receipt

Created: 2026-08-28 07:33 Asia/Shanghai

状态：`OD03_CAPTURED / SPECIFICATION_WORK_ALLOWED / NO_OUTCOME_ACCESS`

## Owner decision

- `OD-03 = A_FOUR_TRADING_WEEKS`
- 入场交易 session 记为 `H0`。
- `H1..H20` 是入场后的后续交易 sessions。
- 若没有更早退出，头寸必须最晚在 `H20` 的预注册管理 checkpoint 进入退出流程。
- Gate A invalidation 或其他已批准的风险条件可在 H20 前触发退出。
- 建仓时只能选择在 `H20_date` 到 `expiration_date` 之间仍至少有 30 个日历日的到期日。
- 在满足其他合约资格的前提下，选择满足上述 expiry-safety 的最早到期日。
- same-expiry strike-up roll 不得重置 H0/H20，不得延长 maximum hold 或 expiry。

## Meaning and non-meaning

这一决定是持仓/退出上限和建仓合约到期资格，不是建仓触发条件。它不决定慢趋势公式、快确认、post-open cutoff、Delta、strike、流动性、数量或 roll trigger。

## Authorized next boundary

下一步只允许完成：

1. `Underlying Normalization Contract`；
2. `Minimum Option Data Gap Contract`；
3. 与两份规格直接相关的 provenance/license/sealed-access 边界。

当前仍不允许读取 outcome/P&L、运行回测、连接 Databento/broker、下载或购买数据、创建或提交订单。

