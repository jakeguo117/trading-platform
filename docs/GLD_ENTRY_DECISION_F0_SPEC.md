# Trading Platform｜GLD Entry Decision f F0 规范

状态：`RESEARCH_ONLY / NO_DECISION_EFFECT / TRIAL_CANDIDATE_NOT_VERIFIED`

## 用户可见结果

Jake运行固定Owner命令后，应能看到一个确定性最终结论，并仅凭Final Decision Card复述：输入是否合格、Entry Gate为何通过或失败、LC0与BCS0分别怎样选出或淘汰、数量受哪个硬上限约束、首选如何确定、以及方案绑定了哪些退出规则。

```bash
PYTHONPATH=src python3 tools/run_gld_entry_decision_f0.py \
  --demo-acceptance \
  --output artifacts/gld-entry-decision-f0-owner-acceptance-v1
```

所有输出固定为：

```text
RESEARCH_ONLY
NO_DECISION_EFFECT
actionable = false
broker_order_count = 0
```

## 唯一候选链路

```text
Validated Entry Facts
+ Required Technical Facts
+ EntryPolicyF0
+ HistoricalStructureEvidenceBundleV2
+ Exit Policy Facts
-> evaluate_entry_decision_f0
-> EntryDecisionResultF0
-> Final Decision Card
```

HTML只读取canonical result，不重新计算。LLM不得生成、补全或改变结论及Decision Trace。

## 固定八阶段Trace

每份合法Result必须按以下顺序且各出现一次：

1. `INPUT_QUALIFICATION`
2. `ENTRY_GATE`
3. `LC0_EVALUATION`
4. `BCS0_EVALUATION`
5. `SIZING`
6. `PREFERENCE`
7. `EXIT_POLICY_BINDING`
8. `FINAL_DECISION`

每个stage记录固定顺序、`PASS | FAIL | ELIMINATED | BLOCKED | NOT_RUN`、事实引用、rule/formula版本、结构化计算、稳定reason code、recovery code及stage hash。前置失败时不得省略后续stage；后续stage以`NOT_RUN`解释阻止原因。

最终Decision状态仅允许：

- `NO_DECISION`：输入或policy authority不足，无法安全判断；
- `NO_ACTION`：输入可评估，但Gate失败、结构证据淘汰或全部安全数量为0；
- `SINGLE_PLAN`：仅一个结构完整合格；
- `PREFERRED_AND_BACKUP`：两个结构完整合格，输出确定首选与解释性备选。

运行异常只出现在外层回执中，状态为`RUN_FAILED`；不得伪造DecisionResult。

## Entry Gate

Trend三项：prior close高于SMA50、SMA50高于SMA200、SMA50 20-session slope为正。Breakout两项：10:44 close高于prior 20-session high、15根确认close中至少10根高于突破线。

预注册四套候选为`3/3+2/2`、`2/3+2/2`、`3/3+1/2`、`2/3+1/2`。R1 synthetic policy固定使用`3/3+2/2`。两组分别达标，任何必要Gate事实缺失都不得被quota绕过。

## 结构资格

LC0在最早安全expiry中选择Delta位于`[450000,550000] ppm`且最接近`500000`的Call；tie-break依次为Delta距离、较高strike、contract ID。coarse/fine必须选择同一contract。

BCS0独立选择同expiry完整1:1组合。long Delta位于`[450000,550000]`、目标`500000`；short位于`[200000,300000]`、目标`250000`；long strike必须低于short strike。tie-break依次为总Delta距离、long距离、short距离、较高long strike、较高short strike、两个contract ID。coarse/fine必须选择同一pair。

两者都要求Call universe完整、报价来自同一原子快照且可执行，并满足`last_trading_date >= H20 + 30 calendar days`。Entry使用ask；BCS short收入使用bid；不得用mid、last或理论价。

Entry date本身必须是固定XNYS日历中的有效session，decision cutoff必须精确等于该session的10:45 ET。每个合约还必须满足`entry_session_date <= last_trading_date <= expiry_date`。Technical Facts V1中的`stressed_max_loss_basis`必须逐结构等于`stressed_entry_debit`，并且stressed debit不得低于同一可执行快照的当前entry debit；不允许用不一致或偏低字段扩大数量。

## Safe Quantity

每个结构独立计算：

```text
planned_loss_per_unit
= stressed_max_loss_basis + planned_exit_fees

safe_quantity
= min(q_kelly, q_account, q_cash, q_delta, q_liquidity, q_external)
```

Kelly、账户损失预算、现金准备金、正向Delta占用、盘口完整单位、外部风险分别形成硬cap。现有负Delta按0处理，不能扩大新多头容量。数量0是合法淘汰，不向上补成1。Drawdown trial factor为：0–5%使用100%，大于5%至10%使用80%，大于10%且小于15%使用40%，15%及以上禁止新Entry。

## Preference

两个结构均完整合格时，先比较：

```text
current_entry_debit_per_unit
* safe_quantity
* expected_net_return_lower_bound_ppm
```

较高者优先；相等时依次选择total planned loss较低、entry cash usage较低者；完全相等时LC0优先。`lower_bound <= 0`、Half-Kelly为0或quantity为0都会淘汰相应结构。旧备选永远不得自动执行。

## 退出政策绑定

每个最终方案绑定Management F0 policy hash、Entry之后第20个XNYS session的10:45 ET H20、`H20+30 calendar days` expiry safety、连续两个完整session的Confirmed Invalidation、独立Hard Stop、expiry safety和account-risk override。输入同时携带Entry session date；validator使用固定XNYS日历逐个数满20个session并重算10:45 ET UTC nanoseconds，不能只接受调用方自报的H20日期。

每个结构的Evidence summary同时绑定产生episode的Entry policy hash、carrier-specific exit policy hash、fee schedule hash及`evidence_max_estimation_outcome_utc_ns`。任一hash与本次theta不一致，或估计使用了Entry cutoff之后的outcome，只淘汰对应结构并说明必须重新估计；Synthetic evidence不得被升级为DATA_QUALIFIED evidence。

R1使用内部fixture验证Hard Stop候选的报价数学：LC读取fresh bid；BCS读取完整`long bid - short ask`，stale、无size、过宽或不同步报价不能触发。该helper不属于公开接口，也不产生生命周期事实；完整的plan、episode、exit-policy、fee及previous-result绑定状态机属于后续Roadmap阶段。

## 完成与未完成的含义

R1验收通过代表：同一合法输入、policy和代码版本产生逐字节相同的JSON、hash、trace和HTML；所有失败路径解释停止位置和恢复方式；broker write始终为0。

输入金额仍限制在signed 64-bit事实域；乘数、数量和ppm形成的派生整数使用64位十进制数字canonical envelope封存，避免合法边界输入在Result阶段意外失败。

R1不代表：参数最优、历史收益成立、真实数据已资格、Active theta存在、真实交易或部署可用。
