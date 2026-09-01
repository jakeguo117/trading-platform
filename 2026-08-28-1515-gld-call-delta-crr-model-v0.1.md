# GLD Call Delta CRR Model Contract v0.1

Created: 2026-08-28 15:15 Asia/Shanghai

状态：`MODEL_DEFINED / SYNTHETIC_IMPLEMENTATION_ALLOWED / REAL_INPUTS_NOT_QUALIFIED`

Model ID：`GLD_CALL_DELTA_CRR_AM_V1`

## 1. Authority boundary

本模型是 Long Call 与 BCS 两条腿唯一可参与 selector 的 Delta 路径。BSM 只作解析测试 oracle；IBKR/Robinhood Greeks 只作 shadow diagnostic。CRR 失败时不得 fallback 到任何 Greek、moneyness 或邻近 strike。

数值引擎只能证明给定规范化输入的确定性计算。rate、distribution、borrow、quote、identity、calendar、expiry 和 source receipt 是否具有真实 point-in-time 权威，必须由引擎外的 qualification 层证明；结构上可计算不等于交易事实合格。

## 2. Canonical inputs

每条 Call 至少需要：

```text
as_of_utc_ns
GLD bid, ask, bid_size, ask_size, ts_event_ns, ts_recv_ns
OCC identity, Call right, American style, standard/unadjusted
strike, exact expiry_utc_ns, multiplier, deliverable, currency
option bid, ask, bid_size, ask_size, tick, ts_event_ns, ts_recv_ns
continuous risk_free_rate r
continuous effective_yield q = GLD expense yield + special borrow yield
rate/distribution/borrow/source version hashes
```

`S = (GLD bid + GLD ask) / 2`，`M = (option bid + option ask) / 2` 只用于 IV/Delta。Entry/exit fill 仍使用可执行 ask/bid。

时间采用 `ACT/365F`：

```text
T = (expiry_utc_ns - as_of_utc_ns) / (365 × 86400 × 1e9)
```

V1 不支持覆盖期内离散 cash distribution。无法证明 distribution coverage 或 borrow/rate 的 point-in-time 输入时，qualification 层必须 fail closed。

## 3. CRR American Call tree

对步数 `N`：

```text
dt = T / N
u = exp(sigma × sqrt(dt))
d = 1 / u
p = (exp((r - q) × dt) - d) / (u - d)
discount = exp(-r × dt)
V_node = max(S_node - K, discount × (p × V_up + (1-p) × V_down))
Delta_root = (V_up_at_t1 - V_down_at_t1) / (S × u - S × d)
```

若 `p` 不在 `[0,1]`、分母无效或任何中间值非有限数，输出 `TREE_PROBABILITY_INVALID` 或 `TREE_NUMERIC_INVALID`。

## 4. IV solve and convergence

- IV bracket 固定为 `[0.0001, 5.0]`。
- 使用固定 `32` 次 bisection；不得根据结果改算法或调用 vendor IV。
- Coarse price/Delta 为 `N=512` 与 `N=513` 的算术平均。
- Fine price/Delta 为 `N=1024` 与 `N=1025` 的算术平均。
- Coarse 与 Fine 各自独立对 option midpoint 反解 IV。
- 每套 price residual 必须 `<= max(1e-6, tick / 100)`。
- 必须满足 `abs(IV_fine - IV_coarse) <= 1e-4`。
- 必须满足 `abs(Delta_fine - Delta_coarse) <= 5e-4`。
- selector 在 coarse/fine 下必须产生相同 winner；否则整张 snapshot 为 `SELECTOR_MODEL_INSTABILITY`。
- 最终权威值取 Fine Delta，使用 decimal `ROUND_HALF_EVEN` 量化为 `delta_ppm = round_half_even(delta × 1,000,000)`。

## 5. Required fail-closed reasons

```text
SNAPSHOT_NOT_CAUSAL
QUOTE_MISSING
QUOTE_STALE
QUOTE_CROSSED
QUOTE_SKEW_EXCEEDED
CONTRACT_IDENTITY_INCOMPLETE
EXPIRATION_INSTANT_UNKNOWN
T_OUT_OF_DOMAIN
RATE_CURVE_PIT_MISSING
RATE_CURVE_GAP
DISTRIBUTION_COVERAGE_MISSING
DISCRETE_DISTRIBUTION_UNSUPPORTED
BORROW_PIT_MISSING
IV_NO_BRACKET
IV_NOT_CONVERGED
IV_UNIDENTIFIABLE
TREE_PROBABILITY_INVALID
TREE_NUMERIC_INVALID
TREE_NOT_CONVERGED
SELECTOR_MODEL_INSTABILITY
MODEL_HASH_MISMATCH
```

## 6. Determinism binding

模型合同、Python source、常量、运行时版本和规范化 inputs 必须进入 model/run hash。相同 hashes 必须产生相同 `iv`、`delta_ppm`、residual、convergence facts 和 reason codes。

## 7. Synthetic acceptance set

- `q=0, r>=0` 的 American Call 向 European BSM price/Delta 收敛。
- 高 q、低 r、深 ITM case 的 American price 不低于 European，并出现 early-exercise node。
- moneyness、30–365 DTE、10%–100% IV 的 price -> IV round trip。
- after-cutoff、missing rate/distribution/borrow、DST 和 expiry boundary 全部 fail closed。
- 同一 canonical fixture 不因来源标签为 IBKR 或 Databento 而改变 `delta_ppm`。
- BCS 的 `net_delta_ppm = long_delta_ppm - short_delta_ppm`，且合格样本满足 `0 < net_delta < long_delta <= 1`。

这些测试只证明算法合同；不证明真实来源、历史 coverage、收益或当前交易可行动。
