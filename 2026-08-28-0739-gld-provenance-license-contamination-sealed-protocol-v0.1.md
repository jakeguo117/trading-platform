# GLD Provenance, License, Contamination and Sealed Access Protocol v0.1

Created: 2026-08-28 07:39 Asia/Shanghai

状态：`PROTOCOL_FROZEN / LEGACY_DEVELOPMENT_ONLY / FORWARD_REQUIRED`

## 1. 核心不变量

- Underlying 和 Option 数据合同分别绑定 `contract_id + contract_sha256 + required_field_set_sha256`；一份通过不能替另一份提供资格。
- 联合样本只有在两份合同对同一日期分区均为 `QUALIFIED` 时才可进入相应阶段。
- 文件名、目录名、`sealed` 标签、人工说明或旧结论均不能升级资格；只有完整的 hash-bound receipt chain 可以。
- 未知 provenance、license、access history 或日期重叠一律 fail closed。
- 旧数据、旧 74 个事件、旧 35 个期权事件及旧项目已查看区间永久 `DEVELOPMENT_ONLY`，不得追溯升级为 Sealed。

## 2. PayloadEvidence 强制记录

```text
PayloadEvidence
  evidence_version
  contract_bindings[]
    contract_id
    contract_sha256
    required_field_set_sha256

  payload_identity
    raw_sha256
    byte_size
    media_type
    schema_version
    normalized_sha256
    transform_code_sha256
    parent_payload_hashes[]

  coverage
    instrument_set_sha256
    start_inclusive
    end_exclusive
    timezone
    calendar_id
    calendar_sha256
    row_count
    missingness_receipt_sha256
    available_at_semantics
    adjustment_and_correction_version

  provenance
    provider_or_source_id
    source_receipt_sha256
    acquisition_timestamp_utc
    acquisition_method
    original_or_derived
    chain_of_custody_events[]

  license
    governing_terms_sha256
    effective_date
    retrieval_date
    entitlement_receipt_sha256
    permitted_use
    non_display_research_allowed
    primary_copy_count
    mirror_and_backup_rights
    retention_period
    termination_and_deletion_rules
    derived_result_rights

  access_events[]
    event_id
    actor_id
    actor_role
    timestamp_utc
    payload_or_partition_sha256
    date_partition
    access_class
    purpose
    outcome_materialized
    receipt_sha256

  contamination
    maximum_allowed_stage
    first_exposure_event_id
    overlapping_prior_intervals[]
    inherited_from_parent_hashes[]
    reason_codes[]

  terminal_status
```

actor、entitlement 和 storage location 可使用脱敏 opaque ID；receipt 不得包含账户、凭证或私人数据。

## 3. Access Classes

- `METADATA_ONLY`：身份、bytes、hash、schema、字段名、dataset-wide 日期；不读行值或结果。
- `DATA_QUALITY_ONLY`：隔离执行器验证格式、缺失、时间单调性；不向策略人员暴露日期级数值或摘要。
- `FEATURE_VALUE_ACCESS`：读取可能影响规则选择的原始值、派生特征或日期级覆盖。
- `OUTCOME_ACCESS`：读取收益、P&L、标签、退出结果、比较、图表、日志或任何 outcome proxy。
- `TERMINAL_RESULT_ACCESS`：在 sealed receipt 原子生成后读取终局。

## 4. 污染传播

- `FEATURE_VALUE_ACCESS` 或 `OUTCOME_ACCESS` 发生后，该 hash、全部派生 hashes 和相应日期区间不得作为 Sealed。
- 策略设计者、参数选择者或 Integrator 在规则冻结前接触日期级值，同样污染。
- 访问日志缺失、身份不明或无法证明未访问，按已污染处理。
- 污染沿 transformation DAG 和日期重叠传播；复制、改名、重压缩或重新 hash 不会洗白。
- `METADATA_ONLY` receipt 既不污染 outcome，也不证明 payload 存在或可运行。

## 5. 旧数据固定分类

```text
data_class = LEGACY_CONTAMINATED
maximum_allowed_stage = DEVELOPMENT
sealed_eligible = false
reason = PRIOR_RESEARCH_OR_ACCESS_HISTORY_NOT_PROVABLY_CLEAN
```

旧数据可用于公式实现、数据质量诊断、Development/walk-forward 和压力测试；不可用于最终阈值证明、Gate 独立确认、Sealed validation 或失败后调参重测同一区间。

## 6. Clean Historical Sealed 证明条件

必须全部满足：

1. 两份数据合同及字段集绑定精确 hash；
2. payload、日期、instrument universe、calendar、transformations 全部 hash-bound；
3. 来源、non-display 研究、保留、镜像、终止、删除权均有 governing evidence；
4. 与污染登记中 Development、diagnosis、pilot、forward 和旧研究区间零重叠；
5. 自取得 payload 起 access log 连续完整，策略人员未接触行值、特征、outcome 或摘要；
6. 规则、代码、环境、指标、stress、缺失处理和 terminal mapping 在访问前冻结；
7. Sealed runner ACL 只许隔离执行器读取，禁止交互查询、中间结果和临时日志泄漏；
8. 预先冻结唯一 `run_id`、次数和 receipt 位置。

预存本地但无法证明完整访问历史的文件默认不是 clean historical Sealed。

## 7. Forward 路线

如果不存在 clean historical interval：

- 先冻结完整规则包及时间戳 `T0`；
- 只收集 T0 后产生的数据；
- 预注册 forward 起止时间和最低完整样本；
- 期间不得根据数据修改规则；
- forward 完成后只运行一次。

当前证据下，本项目终局路线是 `FORWARD_REQUIRED`。

## 8. 一次性 Sealed Run

运行前生成 `SEALED_PREFLIGHT_RECEIPT`，绑定：

- 两份数据合同、raw/normalized payload、日期分区、污染登记、license/entitlement；
- rule package、code、dependency lock、container/environment；
- metrics、stress、missingness handling、terminal mapping；
- runner、auditor、可读取终局的角色；
- `attempt_limit=1`、`retry=0`。

语义：

- 读取第一条 outcome 或生成第一个 aggregate 即消耗该 Sealed 区间；
- 在此前纯基础设施失败可为 `ABORTED_BEFORE_OUTCOME_ACCESS`；只有 hashes 完全不变时可重启；
- outcome 一旦读取，无论崩溃、缺数、Gate fail 或 receipt 不完整，区间都已消耗；
- 不输出中间指标；先原子生成完整 receipt，再允许 Integrator 读终局；
- 独立复算必须预注册为同一 `run_id` 内的第二实现，在同一隔离运行中完成。

失败后不得在同一区间修改规则重跑；只能使用新规则包+新未见区间，或等待新 forward。

## 9. License, Retention and Deletion Gate

license receipt 必须证明：

- governing contract/terms 版本、effective date 和 SHA-256；
- exact dataset/product/market；
- current single-user quantitative research/non-display entitlement；
- primary/mirror/backup 数量和权利；
- retention 期限与终止后处理；
- redistribution 和 derived-result rights；
- OPRA/交易所附加条款；
- 删除、审计、地域和用途限制。

营销页、FAQ 或 explanatory page 不能替代 governing terms。任一字段 `UNKNOWN` 时：

```text
terminal_status = LICENSE_UNQUALIFIED
payload_download_or_retention_allowed = false
sealed_eligible = false
```

需删除时生成不含内容的 `DELETION_RECEIPT`：payload hashes、已删除副本 opaque IDs、时间、执行者、验证方法、剩余副本数和 terms reference。

## 10. Terminal States

```text
PROVENANCE_UNQUALIFIED
LICENSE_UNQUALIFIED
CONTAMINATED_DEVELOPMENT_ONLY
DEVELOPMENT_ELIGIBLE
SEALED_ELIGIBLE
SEALED_PREFLIGHT_BLOCKED
ABORTED_BEFORE_OUTCOME_ACCESS
SEALED_PASS_PENDING_OWNER_ACCEPTANCE
SEALED_FAIL_FINAL
SEALED_INSUFFICIENT_EVIDENCE_FINAL
FORWARD_REQUIRED
```

`SEALED_PASS_PENDING_OWNER_ACCEPTANCE` 仍不是实施、部署、broker 或交易授权。

## 11. 升级为 SEALED_ELIGIBLE 的验收包

- 两份数据合同及 hashes；
- raw/normalized manifest 和 transformation DAG；
- exact coverage/calendar/PIT/missingness receipts；
- source/license/entitlement/retention evidence；
- append-only access/contamination ledger；
- Development 与 Sealed 零重叠报告；
- ACL 和角色隔离证明；
- frozen rule/code/environment/metrics package；
- sealed preflight/one-shot/independent recomputation/terminal receipts；
- Jake 对 exact evidence-package hash 的接受记录。

## 12. 当前终局

```text
PROVENANCE_UNQUALIFIED
+ LICENSE_UNQUALIFIED
+ CONTAMINATED_DEVELOPMENT_ONLY
⇒ FORWARD_REQUIRED
```

不存在可证明干净的历史 Sealed。这不阻止旧数据用于 Development，但阻止它们支撑最终通过。

