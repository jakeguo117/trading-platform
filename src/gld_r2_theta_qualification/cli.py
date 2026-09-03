"""Single synthetic-only CLI for the R2 Wave 1 acceptance bundle."""

from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import sys

from gld_entry_decision_f0 import validate_entry_decision_input_f0
from gld_entry_decision_f0.examples import build_synthetic_entry_decision_case_f0
from gld_management_research.xnys_calendar import (
    action_1045_utc_ns,
    next_session_date,
)

from .contracts import (
    CodeRuntimeBindingV1,
    R2QualificationError,
    build_r1_fixed_policy_projection,
    canonical_json_bytes,
    canonical_sha256,
    derive_base_candidate_sha256,
    derive_development_method_freeze_sha256,
    derive_evaluated_candidate_sha256,
    derive_historical_theta_sha256,
    derive_owner_decision_receipt_sha256,
    derive_qualification_sha256,
    derive_research_freeze_sha256,
)
from .examples import (
    build_synthetic_acceptance_case_v1,
    default_synthetic_acceptance_fixture_path,
    synthetic_acceptance_scenario_specs,
    validate_synthetic_acceptance_fixture,
)
from .forward import (
    build_synthetic_forward_diagnostic_receipt,
    record_synthetic_forward_session,
    record_synthetic_forward_terminal_episode,
    start_synthetic_exact_100_forward,
)
from .policy import (
    PreferenceCandidateV1,
    SyntheticPolicySessionInstructionV1,
    choose_q1_preference,
    evaluate_r2_gate,
    reduce_synthetic_q1_policy_ledger,
    select_bcs0_r2,
    select_lc0_r2,
)
from .publish import (
    ACCEPTANCE_MANIFEST_SCHEMA_VERSION,
    publish_artifacts,
    read_canonical_json_file,
    validate_synthetic_acceptance_manifest,
)
from .registry import build_r2_candidate_registry, validate_r2_candidate_registry
from .render_html import render_engineering_evidence, render_owner_acceptance_index
from .statistics import (
    evaluate_joint_policy_bootstrap,
    evaluate_joint_policy_sensitivities,
    run_carrier_episode_bootstrap,
)
from .stress import (
    QuoteObservationV1,
    SideFeesV1,
    derive_stressed_episode_after_cost,
    evaluate_exit_checkpoint,
)
from .windows import (
    COMMON_DATA_MISSING,
    QUALIFIED,
    assess_common_mask,
    assert_joint_rows_evaluable,
    build_r2_entry_label_windows,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _marker() -> dict[str, object]:
    return {
        "classification": "SYNTHETIC_ONLY",
        "scope": "RESEARCH_ONLY",
        "authority_status": "NO_DECISION_EFFECT",
        "actionable": False,
        "broker_order_count": 0,
        "real_data_accessed": False,
        "real_theta_produced": False,
    }


def _hash(label: str) -> str:
    return sha256(label.encode("ascii")).hexdigest()


def _projection_and_source(scenario_id: str = "both_qualified"):
    r1_case = build_synthetic_entry_decision_case_f0(scenario_id)
    entry_input = r1_case["entry_input"]
    entry_policy = r1_case["entry_policy"]
    return (
        build_r1_fixed_policy_projection(entry_policy),
        validate_entry_decision_input_f0(entry_input),
    )


def _quote(bid: int, ask: int, tick: int = 2) -> QuoteObservationV1:
    return QuoteObservationV1(
        bid_nano_usd=bid,
        ask_nano_usd=ask,
        tick_nano_usd=tick,
        bid_size=10,
        ask_size=10,
        executable=True,
        quote_quality_pass=True,
    )


def _result(
    scenario_id: str,
    reason_code: str,
    actual: dict[str, object],
    detail: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": "GLD_R2_SYNTHETIC_DIAGNOSTIC_RESULT_V1",
        **_marker(),
        "scenario_id": scenario_id,
        "diagnostic_status": "PASS",
        "reason_code": reason_code,
        "actual": actual,
        "detail": detail,
        "scientific_terminal_generated": False,
        "historical_theta_generated": False,
        "qualification_generated": False,
        "owner_receipt_generated": False,
    }


def _diagnose_contract(case: dict[str, object]):
    inputs = case["inputs"]
    assert type(inputs) is dict
    binding = CodeRuntimeBindingV1.from_document(
        {
            "schema_version": "R2_CODE_RUNTIME_BINDING_V1",
            "code_package_sha256": inputs["code_package_sha256"],
            "runtime_id": inputs["runtime_id"],
            "runtime_sha256": inputs["runtime_sha256"],
        }
    )
    projection, _ = _projection_and_source()
    registry = validate_r2_candidate_registry(
        build_r2_candidate_registry(projection).as_dict()
    )
    development_freeze = derive_development_method_freeze_sha256(
        registry_sha256=registry.registry_sha256,
        partition_manifest_sha256=_hash("synthetic-partitions"),
        fee_schedule_manifest_sha256=_hash("synthetic-fees"),
        exit_stress_method_sha256=_hash("synthetic-exit-stress"),
        estimator_method_sha256=_hash("synthetic-estimator"),
        code_package_sha256=binding.code_package_sha256,
        runtime_sha256=binding.runtime_sha256,
    )
    candidate = registry.promotable_candidates[0]
    base_candidate = derive_base_candidate_sha256(
        candidate_id=candidate.candidate_id,
        ordinal=candidate.ordinal,
        trend_required_count=candidate.trend_required_count,
        breakout_required_count=candidate.breakout_required_count,
        stop_mode=candidate.stop_mode,
        hard_stop_loss_ppm=candidate.hard_stop_loss_ppm,
        r1_fixed_policy_projection_sha256=projection.projection_sha256,
        quantity_units=candidate.quantity_units,
    )
    evaluated_candidate = derive_evaluated_candidate_sha256(
        base_candidate_sha256=base_candidate,
        development_method_freeze_sha256=development_freeze,
        carrier_mode="DUAL_PREFERENCE",
        lc0_development_receipt_sha256=_hash("synthetic-lc0-development"),
        bcs0_development_receipt_sha256=_hash("synthetic-bcs0-development"),
    )
    research_freeze = derive_research_freeze_sha256(
        development_method_freeze_sha256=development_freeze,
        evaluated_candidate_sha256s=(evaluated_candidate,),
        common_opportunity_manifest_sha256=_hash(
            "synthetic-common-opportunity-manifest"
        ),
        joint_estimator_method_sha256=_hash("synthetic-joint-estimator"),
        code_package_sha256=binding.code_package_sha256,
        runtime_sha256=binding.runtime_sha256,
    )
    historical_theta = derive_historical_theta_sha256(
        research_freeze_sha256=research_freeze,
        evaluated_candidate_sha256=evaluated_candidate,
        wf_carrier_receipt_sha256s=(
            _hash("synthetic-lc0-wf-receipt"),
            _hash("synthetic-bcs0-wf-receipt"),
        ),
        wf_policy_receipt_sha256=_hash("synthetic-policy-wf-receipt"),
    )
    forward_receipt = _hash("synthetic-forward-receipt")
    qualification = derive_qualification_sha256(
        historical_theta_sha256=historical_theta,
        forward_receipt_sha256=forward_receipt,
        machine_terminal_status="QUALIFIED_POLICY_PENDING_OWNER",
    )
    owner_approve = derive_owner_decision_receipt_sha256(
        qualification_sha256=qualification,
        owner_decision="APPROVE",
    )
    owner_reject = derive_owner_decision_receipt_sha256(
        qualification_sha256=qualification,
        owner_decision="REJECT",
    )
    qualification_after_owner_choices = derive_qualification_sha256(
        historical_theta_sha256=historical_theta,
        forward_receipt_sha256=forward_receipt,
        machine_terminal_status="QUALIFIED_POLICY_PENDING_OWNER",
    )
    encoded = canonical_json_bytes(case)
    round_trip = canonical_json_bytes(json.loads(encoded)) == encoded
    non_cyclic = (
        qualification_after_owner_choices == qualification
        and owner_approve != owner_reject
        and owner_approve != qualification
        and owner_reject != qualification
    )
    if not round_trip or not non_cyclic:
        raise R2QualificationError("R2_SYNTHETIC_IDENTITY_CHAIN_INVALID")
    actual = {
        "canonical_round_trip": round_trip,
        "diagnostic_status": "PASS",
        "identity_chain": "NON_CYCLIC" if non_cyclic else "INVALID",
    }
    detail = {
        "code_runtime_binding_sha256": binding.binding_sha256,
        "r1_fixed_policy_projection_sha256": projection.projection_sha256,
        "synthetic_base_candidate_sha256": base_candidate,
        "synthetic_development_method_freeze_sha256": development_freeze,
        "synthetic_evaluated_candidate_sha256": evaluated_candidate,
        "synthetic_research_freeze_sha256": research_freeze,
        "synthetic_historical_theta_identity_sha256": historical_theta,
        "synthetic_forward_receipt_identity_sha256": forward_receipt,
        "synthetic_qualification_identity_sha256": qualification,
        "synthetic_owner_approve_identity_sha256": owner_approve,
        "synthetic_owner_reject_identity_sha256": owner_reject,
        "owner_choice_changes_qualification": False,
        "synthetic_identity_only": True,
        "historical_theta_generated": False,
        "qualification_generated": False,
    }
    return _result(
        "contract_identity",
        "SYNTHETIC_CONTRACT_DIAGNOSTIC_PASS",
        actual,
        detail,
    ), actual


def _diagnose_registry(case: dict[str, object]):
    inputs = case["inputs"]
    assert type(inputs) is dict
    projection, _ = _projection_and_source()
    registry = build_r2_candidate_registry(projection)
    reparsed = validate_r2_candidate_registry(registry.as_dict())
    promotable = [item.ordinal for item in reparsed.promotable_candidates]
    controls = [item.ordinal for item in reparsed.control_candidates]
    gate_pairs = {
        (item.trend_required_count, item.breakout_required_count)
        for item in reparsed.candidates
    }
    stop_values = [
        item.hard_stop_loss_ppm for item in reparsed.candidates[:4]
    ]
    if (
        len(gate_pairs) != inputs["gate_count"]
        or stop_values != inputs["hard_stop_variants"]
        or any(
            item.quantity_units != inputs["quantity_units"]
            for item in reparsed.candidates
        )
    ):
        raise R2QualificationError("R2_SYNTHETIC_REGISTRY_INPUT_MISMATCH")
    actual = {
        "control_count": len(controls),
        "diagnostic_status": "PASS",
        "promotable_count": len(promotable),
        "registry_count": len(reparsed.candidates),
    }
    detail = {
        "candidate_ids": [item.candidate_id for item in reparsed.candidates],
        "control_ordinals": controls,
        "promotable_ordinals": promotable,
        "registry_sha256": reparsed.registry_sha256,
        "quantity_units": 1,
        "scientific_ranking_generated": False,
    }
    return _result(
        "registry_16_to_12",
        "SYNTHETIC_REGISTRY_DIAGNOSTIC_PASS",
        actual,
        detail,
    ), actual


def _diagnose_windows(case: dict[str, object]):
    inputs = case["inputs"]
    assert type(inputs) is dict
    windows = build_r2_entry_label_windows(
        int(inputs["total_after_warmup_sessions"])
    )
    qualified = int(inputs["qualified_common_rows"])
    missing = int(inputs["missing_common_rows"])
    common_rows = (QUALIFIED,) * qualified + (COMMON_DATA_MISSING,) * missing
    assessment = assess_common_mask(common_rows)
    candidate_rows = {
        "R2C00": ("NO_ACTION",) * qualified
        + (COMMON_DATA_MISSING,) * missing,
        "R2C01": ("ACTION",) * qualified
        + (COMMON_DATA_MISSING,) * missing,
    }
    assert_joint_rows_evaluable(common_rows, candidate_rows)
    actual = {
        "base_entry_sessions": windows.base_entry_sessions,
        "coverage_ppm": assessment.coverage_ppm,
        "development_entry_sessions": windows.development.entry.length,
        "diagnostic_status": "PASS",
        "walk_forward_fold_count": len(windows.walk_forward),
    }
    detail = {
        "common_mask": assessment.as_dict(),
        "windows": windows.as_dict(),
        "candidate_specific_row_drop": False,
    }
    return _result(
        "entry_label_windows",
        "SYNTHETIC_WINDOW_DIAGNOSTIC_PASS",
        actual,
        detail,
    ), actual


def _diagnose_policy(case: dict[str, object]):
    inputs = case["inputs"]
    assert type(inputs) is dict
    projection, source = _projection_and_source(
        str(inputs["r1_synthetic_case_id"])
    )
    gate = evaluate_r2_gate(
        source.entry_gate_facts,
        trend_required_count=3,
        breakout_required_count=2,
    )
    lc0 = select_lc0_r2(source, policy=projection)
    bcs0 = select_bcs0_r2(source, policy=projection)
    preference = choose_q1_preference(
        (
            PreferenceCandidateV1("LC0", 1_000, 20_000, 500, 1_000),
            PreferenceCandidateV1("BCS0", 2_000, 20_000, 400, 800),
        ),
        str(inputs["carrier_mode"]),
    )
    ledger = reduce_synthetic_q1_policy_ledger(
        (
            SyntheticPolicySessionInstructionV1(
                "S1",
                "PASS",
                proposed_episode_id="EP1",
                proposed_carrier_id="BCS0",
            ),
            SyntheticPolicySessionInstructionV1("S2", "NOT_EVALUATED"),
            SyntheticPolicySessionInstructionV1(
                "S3",
                "NOT_EVALUATED",
                terminal_episode_id="EP1",
                terminal_return_ppm=int(inputs["synthetic_terminal_return_ppm"]),
            ),
        )
    )
    fee = int(inputs["fee_per_side_nano_usd"])
    tick = int(inputs["tick_nano_usd"])
    fees = SideFeesV1(fee, fee, fee, fee)
    stressed = derive_stressed_episode_after_cost(
        carrier_id="LC0",
        entry_long_quote=_quote(100, 102, tick),
        exit_long_quote=_quote(150, 152, tick),
        entry_short_quote=None,
        exit_short_quote=None,
        fees=fees,
        multiplier=100,
        episode_id="EP-STRESS",
        fold_id="DEV",
        split="DEVELOPMENT",
        entry_utc_ns=1_000,
        exit_utc_ns=2_000,
        input_sha256=canonical_sha256(case),
    )
    exit_result = evaluate_exit_checkpoint(
        carrier_id="LC0",
        entry_after_cost_basis_nano_usd=10_000,
        long_quote=_quote(40, 42, tick),
        short_quote=None,
        fees=fees,
        multiplier=100,
        hard_stop_loss_ppm=int(inputs["hard_stop_loss_ppm"]),
        confirmed_invalidation=True,
        h20_reached=True,
    )
    actual = {
        "bcs0_selector_status": bcs0.status,
        "diagnostic_status": "PASS",
        "exit_trigger": exit_result.trigger_reason,
        "gate_status": gate.status,
        "lc0_selector_status": lc0.status,
        "ledger_entry_return_ppm": ledger.rows[0].synthetic_return_ppm,
        "preferred_carrier": preference.preferred.carrier_id,
        "stressed_episode_status": stressed.status,
    }
    detail = {
        "gate": gate.as_dict(),
        "lc0_selector": lc0.as_dict(),
        "bcs0_selector": bcs0.as_dict(),
        "preference": preference.as_dict(),
        "policy_ledger": ledger.as_dict(),
        "stressed_episode": stressed.as_dict(),
        "exit_checkpoint": exit_result.as_dict(),
    }
    return _result(
        "policy_q1_stress",
        "SYNTHETIC_POLICY_DIAGNOSTIC_PASS",
        actual,
        detail,
    ), actual


def _diagnose_statistics(case: dict[str, object]):
    inputs = case["inputs"]
    assert type(inputs) is dict
    episode_count = int(inputs["carrier_episode_count"])
    carrier_return = int(inputs["carrier_return_ppm"])
    replicates = int(inputs["bootstrap_replicates"])
    block_length = int(inputs["block_length"])
    freeze = canonical_sha256(case)
    carrier = run_carrier_episode_bootstrap(
        (carrier_return,) * episode_count,
        seed_sha256=freeze,
        replicates=replicates,
        block_length=block_length,
    )
    candidate_returns = tuple(int(value) for value in inputs["candidate_returns_ppm"])
    fold_rows = {
        f"WF{fold}": (candidate_returns,) * int(inputs["fold_row_count"])
        for fold in range(1, int(inputs["fold_count"]) + 1)
    }
    joint = evaluate_joint_policy_bootstrap(
        fold_rows,
        candidate_ids=("R2C00", "R2C01"),
        research_freeze_sha256=freeze,
        replicates=replicates,
        block_length=block_length,
    )
    sensitivities = evaluate_joint_policy_sensitivities(
        fold_rows,
        candidate_ids=("R2C00", "R2C01"),
        research_freeze_sha256=freeze,
        replicates=replicates,
    )
    actual = {
        "block_length": carrier.block_length,
        "bootstrap_replicates": carrier.replicates,
        "carrier_lower_bound_ppm": carrier.lower_bound_ppm,
        "diagnostic_status": "PASS",
        "joint_candidate_passes": list(joint.candidate_passes),
        "joint_lower_bounds_ppm": list(joint.simultaneous_lower_bounds_ppm),
    }
    detail = {
        "carrier_distribution_sha256": canonical_sha256(
            list(carrier.bootstrap_means_ppm)
        ),
        "carrier_half_kelly_ppm": carrier.half_kelly_ppm,
        "joint_critical_value_ppm": joint.critical_value_ppm,
        "joint_seed_contract_sha256": joint.seed_contract_sha256,
        "report_only_block_lengths": [item.block_length for item in sensitivities],
        "report_only_qualification_eligible": [
            item.qualification_eligible for item in sensitivities
        ],
    }
    return _result(
        "joint_statistics",
        "SYNTHETIC_STATISTICS_DIAGNOSTIC_PASS",
        actual,
        detail,
    ), actual


def _session_dates(count: int, first_session: date) -> tuple[date, ...]:
    values = [first_session]
    while len(values) < count:
        values.append(next_session_date(values[-1]))
    return tuple(values)


def _diagnose_forward(case: dict[str, object]):
    inputs = case["inputs"]
    assert type(inputs) is dict
    dates = _session_dates(
        int(inputs["exact_terminal_episode_target"]),
        date.fromisoformat(str(inputs["t0_session_date"])),
    )
    theta_hash = _hash("synthetic-historical-theta-not-real")
    t0 = action_1045_utc_ns(dates[0]) - 1
    state = start_synthetic_exact_100_forward(
        synthetic_theta_sha256=theta_hash,
        t0_utc_ns=t0,
    )
    for ordinal, session_date in enumerate(dates, start=1):
        session_id = f"XNYS-{session_date.isoformat()}"
        state = record_synthetic_forward_session(
            state,
            session_id=session_id,
            session_date=session_date.isoformat(),
            session_ordinal=ordinal,
            observation_utc_ns=action_1045_utc_ns(session_date),
            input_sha256=_hash(f"synthetic-forward-session-{ordinal}"),
            qualified=True,
        )
    for ordinal, session_date in enumerate(dates, start=1):
        session_id = f"XNYS-{session_date.isoformat()}"
        state = record_synthetic_forward_terminal_episode(
            state,
            episode_id=f"EP-{ordinal:03d}",
            entry_session_id=session_id,
            exit_session_id=session_id,
            exit_utc_ns=action_1045_utc_ns(session_date),
            status="COMPLETE",
            synthetic_return_ppm=int(inputs["synthetic_return_ppm"]),
            input_sha256=_hash(f"synthetic-forward-episode-{ordinal}"),
        )
    diagnostic = build_synthetic_forward_diagnostic_receipt(
        state,
        synthetic_scenario_sha256=canonical_sha256(case),
    )
    if state.coverage_ppm < int(inputs["minimum_coverage_ppm"]):
        raise R2QualificationError("R2_SYNTHETIC_FORWARD_COVERAGE_MISMATCH")
    actual = {
        "coverage_ppm": state.coverage_ppm,
        "diagnostic_status": "PASS",
        "exact_episode_count": state.terminal_episode_count,
        "forward_lower_bound_ppm": diagnostic.synthetic_lower_bound_ppm,
        "sealed": state.sealed,
    }
    detail = {
        "collection_status": state.collection_status,
        "diagnostic_receipt": diagnostic.as_dict(),
        "sealed_manifest_sha256": state.sealed_manifest_sha256,
        "scientific_forward_receipt_generated": False,
    }
    return _result(
        "forward_exact_100",
        "SYNTHETIC_FORWARD_DIAGNOSTIC_PASS",
        actual,
        detail,
    ), actual


def _run_scenario(case: dict[str, object]):
    scenario_id = case["scenario_id"]
    if scenario_id == "contract_identity":
        return _diagnose_contract(case)
    if scenario_id == "registry_16_to_12":
        return _diagnose_registry(case)
    if scenario_id == "entry_label_windows":
        return _diagnose_windows(case)
    if scenario_id == "policy_q1_stress":
        return _diagnose_policy(case)
    if scenario_id == "joint_statistics":
        return _diagnose_statistics(case)
    if scenario_id == "forward_exact_100":
        return _diagnose_forward(case)
    raise R2QualificationError("R2_SYNTHETIC_SCENARIO_UNKNOWN")


def _run_all(specifications: tuple[dict[str, object], ...]):
    rows = []
    for specification in specifications:
        scenario_id = specification["scenario_id"]
        assert type(scenario_id) is str
        case = build_synthetic_acceptance_case_v1(scenario_id)
        result, actual = _run_scenario(case)
        rows.append((case, result, actual))
    return tuple(rows)


def _r1_regression_receipt(fixture: dict[str, object]) -> dict[str, object]:
    raw_regression = fixture["r1_regression"]
    assert type(raw_regression) is dict
    tree_relative = Path(str(raw_regression["tree_root"]))
    tree_root = _PROJECT_ROOT / tree_relative
    resolved_tree_root = tree_root.resolve()
    if (
        not resolved_tree_root.is_relative_to(_PROJECT_ROOT)
        or not tree_root.is_dir()
        or tree_root.is_symlink()
    ):
        raise R2QualificationError("R1_ACCEPTED_ASSET_MISSING")
    tree_files = tuple(
        candidate
        for candidate in sorted(tree_root.rglob("*"))
        if candidate.is_file()
    )
    if any(candidate.is_symlink() for candidate in tree_files):
        raise R2QualificationError("R1_ACCEPTED_ASSET_INVALID")
    tree_rows = [
        {
            "path": candidate.relative_to(tree_root).as_posix(),
            "sha256": sha256(candidate.read_bytes()).hexdigest(),
        }
        for candidate in tree_files
    ]
    tree_digest_document = {
        "schema_version": "GLD_R1_ACCEPTANCE_TREE_DIGEST_V1",
        "file_count": len(tree_rows),
        "files": tree_rows,
    }
    tree_hash = canonical_sha256(tree_digest_document)
    if (
        len(tree_rows) != raw_regression["tree_file_count"]
        or tree_hash != raw_regression["tree_sha256"]
    ):
        raise R2QualificationError("R1_ACCEPTED_BYTES_CHANGED")
    assets: list[dict[str, object]] = []
    for candidate, tree_row in zip(tree_files, tree_rows, strict=True):
        digest = tree_row["sha256"]
        assets.append(
            {
                "classification": "SYNTHETIC_ONLY",
                "role": "TREE_FILE",
                "path": candidate.relative_to(_PROJECT_ROOT).as_posix(),
                "expected_sha256": digest,
                "actual_sha256": digest,
                "status": "BYTE_IDENTICAL",
            }
        )
    owner_relative = Path(str(raw_regression["owner_receipt_path"]))
    owner_target = _PROJECT_ROOT / owner_relative
    resolved_owner = owner_target.resolve()
    if (
        not resolved_owner.is_relative_to(_PROJECT_ROOT)
        or not owner_target.is_file()
        or owner_target.is_symlink()
    ):
        raise R2QualificationError("R1_ACCEPTED_ASSET_MISSING")
    owner_hash = sha256(owner_target.read_bytes()).hexdigest()
    if owner_hash != raw_regression["owner_receipt_sha256"]:
        raise R2QualificationError("R1_ACCEPTED_BYTES_CHANGED")
    assets.append(
        {
            "classification": "SYNTHETIC_ONLY",
            "role": "OWNER_RECEIPT",
            "path": owner_relative.as_posix(),
            "expected_sha256": owner_hash,
            "actual_sha256": owner_hash,
            "status": "BYTE_IDENTICAL",
        }
    )
    body = {
        "schema_version": "GLD_R2_R1_REGRESSION_RECEIPT_V1",
        "classification": "SYNTHETIC_ONLY",
        "accepted_commit": raw_regression["accepted_commit"],
        "status": "R1_ACCEPTED_BYTES_UNCHANGED",
        "tree_root": tree_relative.as_posix(),
        "tree_file_count": len(tree_rows),
        "tree_sha256": tree_hash,
        "assets": assets,
    }
    return {**body, "receipt_sha256": canonical_sha256(body)}


def _determinism_receipt(first: object, second: object) -> dict[str, object]:
    first_hash = canonical_sha256(first)
    second_hash = canonical_sha256(second)
    if first_hash != second_hash:
        raise R2QualificationError("R2_SYNTHETIC_DIAGNOSTIC_NONDETERMINISTIC")
    body = {
        "schema_version": "GLD_R2_DETERMINISM_RECEIPT_V1",
        "classification": "SYNTHETIC_ONLY",
        "status": "REPEATED_SYNTHETIC_DIAGNOSTICS_BYTE_IDENTICAL",
        "first_run_sha256": first_hash,
        "second_run_sha256": second_hash,
    }
    return {**body, "receipt_sha256": canonical_sha256(body)}


def _manifest_with_hash(body: dict[str, object]) -> dict[str, object]:
    return {**body, "manifest_sha256": canonical_sha256(body)}


def build_acceptance_artifacts(
    fixture_path: Path | None = None,
) -> dict[str, bytes]:
    """Build one complete in-memory Wave 1 bundle without external access."""

    source_path = (
        default_synthetic_acceptance_fixture_path()
        if fixture_path is None
        else fixture_path
    )
    fixture = validate_synthetic_acceptance_fixture(
        read_canonical_json_file(source_path)
    )
    specifications = synthetic_acceptance_scenario_specs(fixture)
    first = _run_all(specifications)
    second = _run_all(specifications)
    first_snapshot = [
        {"input": case, "result": result, "actual": actual}
        for case, result, actual in first
    ]
    second_snapshot = [
        {"input": case, "result": result, "actual": actual}
        for case, result, actual in second
    ]
    determinism = _determinism_receipt(first_snapshot, second_snapshot)
    artifacts: dict[str, bytes] = {}
    scenario_rows: list[dict[str, object]] = []
    for specification, (case, result, actual) in zip(
        specifications,
        first,
        strict=True,
    ):
        expected = specification["expected"]
        if actual != expected:
            raise R2QualificationError("R2_ACCEPTANCE_SCENARIO_MISMATCH")
        scenario_id = str(specification["scenario_id"])
        input_path = f"scenarios/{scenario_id}/input.json"
        result_path = f"scenarios/{scenario_id}/result.json"
        input_payload = canonical_json_bytes(case) + b"\n"
        result_payload = canonical_json_bytes(result) + b"\n"
        artifacts[input_path] = input_payload
        artifacts[result_path] = result_payload
        scenario_rows.append(
            {
                "scenario_id": scenario_id,
                "title_zh": specification["title_zh"],
                "classification": "SYNTHETIC_ONLY",
                "expected": expected,
                "actual": actual,
                "reason_code": specification["reason_code"],
                "automated_evidence_status": "AUTOMATED_EVIDENCE_PASS",
                "owner_status": "OWNER_ACCEPTANCE_PENDING",
                "input_sha256": canonical_sha256(case),
                "result_sha256": canonical_sha256(result),
                "artifact_hashes": {
                    "input_file_sha256": sha256(input_payload).hexdigest(),
                    "result_file_sha256": sha256(result_payload).hexdigest(),
                },
                "input_path": input_path,
                "result_path": result_path,
                "test_boundary_zh": specification["test_boundary_zh"],
            }
        )
    core = {
        "schema_version": ACCEPTANCE_MANIFEST_SCHEMA_VERSION,
        **_marker(),
        "status": "R2_INFRASTRUCTURE_VERIFIED",
        "next": "METADATA_AUTHORIZATION_REQUIRED",
        "scientific_terminal_generated": False,
        "qualification_generated": False,
        "owner_receipt_generated": False,
        "owner_status": "OWNER_ACCEPTANCE_PENDING",
        "candidate_flow": {
            "registry_count": 16,
            "promotable_count": 12,
            "control_count": 4,
            "evaluated_k": None,
            "evaluated_k_status": "NOT_RUN_REAL_DATA",
            "historical_theta_count": 0,
            "forward_theta_count": 0,
            "final_theta_count": 0,
        },
        "r1_regression_receipt": _r1_regression_receipt(fixture),
        "determinism_receipt": determinism,
        "scenarios": scenario_rows,
    }
    provisional = _manifest_with_hash(
        {
            **core,
            "rendered_artifacts": [
                {"path": "index.html", "sha256": "0" * 64},
                {"path": "engineering-evidence.html", "sha256": "0" * 64},
            ],
        }
    )
    sealed_provisional = validate_synthetic_acceptance_manifest(provisional)
    owner_payload = render_owner_acceptance_index(sealed_provisional).encode("utf-8")
    engineering_payload = render_engineering_evidence(
        sealed_provisional
    ).encode("utf-8")
    final_manifest = _manifest_with_hash(
        {
            **core,
            "rendered_artifacts": [
                {"path": "index.html", "sha256": sha256(owner_payload).hexdigest()},
                {
                    "path": "engineering-evidence.html",
                    "sha256": sha256(engineering_payload).hexdigest(),
                },
            ],
        }
    )
    sealed_final = validate_synthetic_acceptance_manifest(final_manifest)
    final_owner_payload = render_owner_acceptance_index(sealed_final).encode("utf-8")
    final_engineering_payload = render_engineering_evidence(sealed_final).encode(
        "utf-8"
    )
    if (
        final_owner_payload != owner_payload
        or final_engineering_payload != engineering_payload
    ):
        raise R2QualificationError("R2_ACCEPTANCE_RENDER_PROJECTION_CHANGED")
    artifacts["acceptance-manifest.json"] = (
        canonical_json_bytes(final_manifest) + b"\n"
    )
    artifacts["index.html"] = final_owner_payload
    artifacts["engineering-evidence.html"] = final_engineering_payload
    return artifacts


def _failure_receipt(reason_code: str) -> dict[str, object]:
    body = {
        "schema_version": "GLD_R2_SYNTHETIC_RUN_RECEIPT_V1",
        "classification": "SYNTHETIC_ONLY",
        "scope": "RESEARCH_ONLY",
        "authority_status": "NO_DECISION_EFFECT",
        "run_status": "RUN_FAILED",
        "reason_code": reason_code,
        "scientific_terminal_generated": False,
        "historical_theta_generated": False,
        "qualification_generated": False,
        "owner_receipt_generated": False,
        "actionable": False,
        "broker_order_count": 0,
        "real_data_accessed": False,
        "real_theta_produced": False,
    }
    return {**body, "receipt_sha256": canonical_sha256(body)}


def _publish_failure(output: Path, reason_code: str) -> None:
    receipt = _failure_receipt(reason_code)
    publish_artifacts(
        output,
        {"run-receipt.json": canonical_json_bytes(receipt) + b"\n"},
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_gld_r2_theta_qualification",
        description=(
            "Run deterministic synthetic-only R2 Wave 1 infrastructure diagnostics."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo-acceptance", action="store_true")
    source.add_argument("--fixture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    fixture_path = None if arguments.demo_acceptance else arguments.fixture
    try:
        artifacts = build_acceptance_artifacts(fixture_path)
        publish_artifacts(arguments.output, artifacts)
    except R2QualificationError as exc:
        try:
            _publish_failure(arguments.output, exc.reason_code)
        except R2QualificationError:
            pass
        print(f"ERROR {exc.reason_code}", file=sys.stderr)
        return 2
    except Exception as exc:
        reason_code = "UNEXPECTED_LOCAL_ERROR"
        try:
            _publish_failure(arguments.output, reason_code)
        except R2QualificationError:
            pass
        print(f"ERROR {reason_code} {type(exc).__name__}", file=sys.stderr)
        return 2
    print(str(arguments.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_acceptance_artifacts", "main"]
