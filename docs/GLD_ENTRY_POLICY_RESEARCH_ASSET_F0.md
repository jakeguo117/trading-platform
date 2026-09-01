# GLD Entry Policy Research Asset F0

状态：`RESEARCH_ONLY / OWNER_ACCEPTED_COMPLETE / POLICY_WINNER_NULL / ACTIVE_ENTRY_POLICY_NOT_AVAILABLE`

本文件固定保存R1中被程序实现但尚未由真实outcome验证的Entry政策候选。它是后续R2研究的输入目录，不是Active policy，也不能授权交易。

## Policy候选目录

| 领域 | R1 synthetic使用值 | 其他预注册候选 | 当前证据状态 |
|---|---|---|---|
| Gate | Trend `3/3` + Breakout `2/2` | `2/3+2/2`、`3/3+1/2`、`2/3+1/2` | `TRIAL_CANDIDATE_NOT_VERIFIED` |
| LC0 Delta | `[450000,550000]`, target `500000 ppm` | none in R1 | `TRIAL_CANDIDATE_NOT_VERIFIED` |
| BCS0 long Delta | `[450000,550000]`, target `500000 ppm` | none in R1 | `TRIAL_CANDIDATE_NOT_VERIFIED` |
| BCS0 short Delta | `[200000,300000]`, target `250000 ppm` | none in R1 | `TRIAL_CANDIDATE_NOT_VERIFIED` |
| Account loss budget | eligible bankroll的`100000 ppm` | future R2 candidates | `SYNTHETIC_FIXTURE_ONLY` |
| Drawdown factor | `100% / 80% / 40% / 0%` | future R2 candidates | `TRIAL_CANDIDATE_NOT_VERIFIED` |
| Hard Stop | LC0与BCS0分别`500000 ppm` | `333333`、`666667`、no-stop control | `TRIAL_CANDIDATE_NOT_VERIFIED` |
| Preference | lower-bound expected total net profit | planned loss、cash、LC exact tie | `TRIAL_CANDIDATE_NOT_VERIFIED` |

## Evidence V2预注册方法

- LC0与BCS0使用完全独立的carrier-specific episode source binding、distribution与Kelly receipt；共享底层市场事实如需支持，必须在后续版本拆出单独的shared-market hash，不能复用carrier episode hash。
- 每份carrier evidence必须绑定产生episode的完整Entry policy hash、独立exit policy hash和fee schedule hash；Gate、Delta、Hard Stop或费用版本变化后，旧distribution与Kelly不得复用。
- summary显式携带最终估计使用的最晚outcome UTC nanoseconds；它不得晚于本次Entry 10:45 ET cutoff。sealed OOS继续明确排除，不得暗中进入估计。
- Entry按ask，Exit按bid；BCS始终计算两条腿；opening和closing fees全部计入。
- development和sealed OOS都不进入最终估计。
- 最低要求：100个OOS episodes、3个有效fold、95% coverage。
- 每个fold显式记录training cutoff、test start及outcome cutoff；WF episode的entry与exit都必须落在自己的窗口内，相邻fold之间保留purge gap，development必须在首个test前结束，sealed OOS必须在最后outcome cutoff后开始。
- circular moving-block bootstrap固定block length 20、10,000 replicates；block起点由输入hash确定性派生，不调用系统随机数。
- one-sided 5% lower bound取排序后第500个结果。
- Full-Kelly只在所有wealth factor严格为正的整数ppm域中求解。
- Robust Full-Kelly取原始Full-Kelly和bootstrap Kelly 5%分位的较小值。
- lower bound不为正时Kelly归零；Half-Kelly为Robust Full-Kelly整除2。
- R1 V2设置显式bootstrap work bound；超过边界时稳定拒绝而不截断或降低10,000次replicates。Kelly最终相邻整数比较使用完整固定的Decimal context，不继承调用进程的rounding或trap状态。

## R1/R2晋升边界

R1的synthetic结果只能证明估计器和决策函数可计算、可重复、可审核。R1固定输出：

```text
policy_winner = null
ACTIVE_ENTRY_POLICY_NOT_AVAILABLE
```

只有R2完成真实数据资格、after-cost walk-forward、sealed/OOS验证、稳定性与失败边界审查，并由Jake明确批准后，候选policy才可能产生单一Active theta。程序不得根据当日表现、shadow control或synthetic结果自动晋升。

## R1 Owner人工验收

Jake于2026-09-01明确回复“R1 1–10全部认同”。该批准接受以下本地synthetic产品行为：缺数据与Gate失败可区分、Trend/Breakout分组Gate、LC0/BCS0独立选择、单结构输出、六项硬cap数量、确定Preference、完整退出政策绑定、八阶段可解释trace、停止位置与恢复方式，以及相同输入的确定性与零broker写入。

人工状态记录为`OWNER_ACCEPTED_COMPLETE`，由追加式Owner Acceptance Receipt绑定验收前的本文件hash、正式acceptance manifest与Owner首页。这个状态不改变本页任何候选的`TRIAL_CANDIDATE_NOT_VERIFIED`，也不把`policy_winner = null`或`ACTIVE_ENTRY_POLICY_NOT_AVAILABLE`升级为Active theta。
