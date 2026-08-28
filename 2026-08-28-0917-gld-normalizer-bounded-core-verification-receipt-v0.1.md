# GLD Normalizer Bounded Core Verification Receipt v0.1

Created: 2026-08-28 09:17 Asia/Shanghai

状态：`LOCAL_VERIFIED / SYNTHETIC_FIXTURE_ONLY / REAL_SOURCE_NOT_IMPLEMENTED / AUTHORITY_UNAVAILABLE / NO_DECISION`

## 已真实完成

- 以 exact integer nanoseconds 实现 OD-04 的 `[09:30:00, 09:31:00) America/New_York` 半开区间分类，并覆盖 EST/EDT 与四个边界点。
- 实现有硬上限、全文件验证成功后才原子返回的 synthetic CSV/DBN fixture decoder。
- DBN metadata 只按 exact length + SHA-256 绑定为 opaque bytes；没有宣称解析其业务语义。
- 实现 fixture-bound source-record hash、因果 cutoff 检查、严格有界的内存去重及冲突重传 fail-closed。
- 实现 calendar payload/receipt 的结构与 hash binding 检查；结果明确 `authority_qualified=false`。
- 对 payload、record count、dedup、calendar sessions、control text、proof bytes、整数 sentinel 和 timestamp 设定硬上限。

## 明确没有完成

- 没有真实文件 path reader、zstd/DBN source reader、atomic staging、provider query、网络或 Databento 连接。
- 没有权威 actual-session calendar、临时休市 overlay、halt/reopen/auction phase receipt 或 H0–H20 映射。
- 没有 final canonical market fact，也没有任何 Entry signal、交易决策卡、期权合约、数量、退出、roll 或 broker payload。
- 没有读取 outcome、P&L、真实市场行或账户数据。

因此这份实现只证明冻结语义可以被安全编码和测试，不证明数据已经合格，也不产生 `TRADE` 或 `NO_TRADE` 结论。

## 验证证据

- `python3 -m unittest discover -v`：`34/34 PASS`。
- `python3 -m compileall -q src tests`：`PASS`。
- 独立代码复核：`PASS`。
- 独立安全复核：`PASS`；结论只适用于 synthetic fixture / non-authoritative 范围。

## Hash manifest

| Artifact | SHA-256 |
|---|---|
| `pyproject.toml` | `b717c223495817ee0558432a9bf111210cce7e4661ddf0b8ecb091b2ef67534f` |
| `src/gld_normalizer/readers.py` | `695f97ef2ed21d1bd1830d999945598f02e34a716c38f4ac0771d311f1da2cef` |
| `src/gld_normalizer/canonical.py` | `dba2060188c75dab18dffbfa6976c57d2d2608f3474fe0711e762987134483bc` |
| `src/gld_normalizer/time_semantics.py` | `757ff65938a132732a9fbe67ea76ecd4f24ca1a2722a362cf5918562a8761770` |
| `tests/test_readers.py` | `f6e1d631f38deeb4655e078c38e53aa2e6fd4f13b5aef75f08eb2cb7ce410fdc` |
| `tests/test_canonical.py` | `095f093506b7e331baf35a27b0bfcc0623ca9975877624f210fdb28693fc1d94` |
| `tests/test_time_semantics.py` | `6171e270633f2d24c537851bf3d1506fe2e2c172dddecb7bbe90dd43a3ff7486` |

## Next owner gate

OD-05 只选择一个或两个 post-open cutoff 候选。Owner 决定之前不冻结快确认窗口，也不读取 outcome/P&L。

