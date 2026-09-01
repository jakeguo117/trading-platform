"""Fixed local command for GLD Entry Decision R1 evidence and Owner review."""

from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys

from .canonical import canonical_json_bytes, canonical_json_sha256
from .contracts import validate_entry_decision_input_f0
from .errors import EntryDecisionF0Error
from .examples import (
    ACCEPTANCE_SCENARIOS_F0,
    build_synthetic_entry_decision_case_f0,
    synthetic_evidence_material_f0,
)
from .policy import validate_entry_policy_f0
from .publish import publish_artifacts, read_canonical_json_file
from .render_html import (
    render_engineering_evidence,
    render_final_decision_card,
    render_owner_acceptance_index,
)


_CLI_INPUT_KEYS = frozenset({"schema_version", "entry_input", "entry_policy"})
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REFERENCE_ASSETS = (
    ("GLD_ENTRY_DECISION_F0_SPEC.md", _PROJECT_ROOT / "docs" / "GLD_ENTRY_DECISION_F0_SPEC.md"),
    (
        "GLD_ENTRY_POLICY_RESEARCH_ASSET_F0.md",
        _PROJECT_ROOT / "docs" / "GLD_ENTRY_POLICY_RESEARCH_ASSET_F0.md",
    ),
)


def _evaluate(entry_input: object, entry_policy: object) -> object:
    # Import the public kernel lazily so the CLI remains a leaf of the package.
    from . import evaluate_entry_decision_f0

    return evaluate_entry_decision_f0(entry_input, entry_policy)


def _as_result_document(result: object) -> dict[str, object]:
    if hasattr(result, "as_dict") and callable(result.as_dict):
        result = result.as_dict()
    if type(result) is not dict:
        raise EntryDecisionF0Error("CLI_DECISION_RESULT_INVALID")
    return result


def _normalize_case(document: object) -> tuple[dict[str, object], dict[str, object]]:
    if type(document) is not dict or frozenset(document) != _CLI_INPUT_KEYS:
        raise EntryDecisionF0Error("CLI_INPUT_SCHEMA_INVALID")
    if document["schema_version"] != "ENTRY_DECISION_CLI_INPUT_F0_V1":
        raise EntryDecisionF0Error("CLI_INPUT_VERSION_INVALID")
    normalized_input = validate_entry_decision_input_f0(document["entry_input"])
    normalized_policy = validate_entry_policy_f0(document["entry_policy"])
    normalized_case = {
        "schema_version": "ENTRY_DECISION_CLI_INPUT_F0_V1",
        "entry_input": normalized_input.as_dict(),
        "entry_policy": normalized_policy.as_dict(),
    }
    return normalized_case, _as_result_document(
        _evaluate(normalized_input.as_dict(), normalized_policy.as_dict())
    )


def _trace_document(result: dict[str, object]) -> dict[str, object]:
    trace = result.get("decision_trace")
    if type(trace) is not dict:
        raise EntryDecisionF0Error("CLI_DECISION_TRACE_INVALID")
    return trace


def _plan_carriers(result: dict[str, object]) -> tuple[str, ...]:
    plans = result.get("plans")
    if type(plans) is not dict or frozenset(plans) != frozenset({"preferred", "backup"}):
        raise EntryDecisionF0Error("CLI_DECISION_PLAN_SET_INVALID")
    carriers: list[str] = []
    for plan in (plans.get("preferred"), plans.get("backup")):
        if plan is None:
            continue
        if type(plan) is not dict or type(plan.get("carrier_id")) is not str:
            raise EntryDecisionF0Error("CLI_DECISION_PLAN_SET_INVALID")
        carriers.append(plan["carrier_id"])
    return tuple(carriers)


def _expected_text(specification: dict[str, object]) -> str:
    status = specification["expected_status"]
    carriers = specification["expected_carriers"]
    if type(carriers) is not tuple:
        raise EntryDecisionF0Error("ACCEPTANCE_SPEC_INVALID")
    carrier_text = "无方案" if not carriers else " → ".join(str(value) for value in carriers)
    return f"{status}；{carrier_text}"


def _actual_text(result: dict[str, object]) -> str:
    status = result.get("decision_status")
    reason = result.get("reason_code")
    carriers = _plan_carriers(result)
    if type(status) is not str or type(reason) is not str:
        raise EntryDecisionF0Error("CLI_DECISION_RESULT_INVALID")
    carrier_text = "无方案" if not carriers else " → ".join(carriers)
    return f"{status}；{carrier_text}；reason={reason}"


def _actual_text_zh(result: dict[str, object]) -> str:
    status = result.get("decision_status")
    carriers = _plan_carriers(result)
    if status == "NO_DECISION":
        return "事实或policy authority不足，程序不形成判断。"
    if status == "NO_ACTION":
        return "事实已评估，但没有合格且数量大于0的方案。"
    if status == "SINGLE_PLAN" and len(carriers) == 1:
        return f"只有{carriers[0]}合格，程序只输出这一套方案。"
    if status == "PREFERRED_AND_BACKUP" and len(carriers) == 2:
        return f"程序输出{carriers[0]}为首选、{carriers[1]}为备选。"
    raise EntryDecisionF0Error("CLI_DECISION_SUMMARY_INVALID")


def _result_evidence_valid(result: dict[str, object]) -> bool:
    try:
        render_final_decision_card(result)
    except EntryDecisionF0Error:
        return False
    result_hash = result.get("result_sha256")
    trace = _trace_document(result)
    trace_hash = trace.get("trace_sha256")
    unsigned_result = dict(result)
    unsigned_result.pop("result_sha256", None)
    unsigned_trace = dict(trace)
    unsigned_trace.pop("trace_sha256", None)
    stages = trace.get("stages")
    if type(stages) is not list:
        return False
    stage_hashes_valid = True
    for stage in stages:
        if type(stage) is not dict:
            return False
        unsigned_stage = dict(stage)
        stage_hash = unsigned_stage.pop("stage_sha256", None)
        if type(stage_hash) is not str or stage_hash != canonical_json_sha256(unsigned_stage):
            stage_hashes_valid = False
            break
    return (
        type(result_hash) is str
        and _SHA256_RE.fullmatch(result_hash) is not None
        and result_hash == canonical_json_sha256(unsigned_result)
        and type(trace_hash) is str
        and _SHA256_RE.fullmatch(trace_hash) is not None
        and trace_hash == canonical_json_sha256(unsigned_trace)
        and stage_hashes_valid
        and result.get("classification") == "RESEARCH_ONLY"
        and result.get("authority_status") == "NO_DECISION_EFFECT"
        and result.get("actionable") is False
        and result.get("broker_order_count") == 0
    )


def _scenario_business_evidence(
    scenario_id: str,
    result: dict[str, object],
) -> bool:
    trace = _trace_document(result)
    raw_stages = trace.get("stages")
    if type(raw_stages) is not list or len(raw_stages) != 8:
        return False
    stages = {
        stage.get("stage_id"): stage
        for stage in raw_stages
        if type(stage) is dict and type(stage.get("stage_id")) is str
    }
    if len(stages) != 8:
        return False

    def sizing_caps_are_minimum() -> bool:
        calculation = stages["SIZING"].get("calculation")
        if type(calculation) is not dict:
            return False
        structures = calculation.get("structures")
        if type(structures) is not dict or not structures:
            return False
        cap_names = (
            "q_kelly",
            "q_account",
            "q_cash",
            "q_delta",
            "q_liquidity",
            "q_external",
        )
        for value in structures.values():
            if type(value) is not dict:
                return False
            caps = tuple(value.get(name) for name in cap_names)
            safe_quantity = value.get("safe_quantity")
            binding_caps = value.get("binding_caps")
            if (
                any(type(cap) is not int for cap in caps)
                or type(safe_quantity) is not int
                or safe_quantity != min(caps)
                or type(binding_caps) is not list
                or frozenset(binding_caps)
                != frozenset(
                    name for name, cap in zip(cap_names, caps) if cap == safe_quantity
                )
            ):
                return False
        return True

    def exit_binding_is_complete() -> bool:
        plans = result.get("plans")
        calculation = stages["EXIT_POLICY_BINDING"].get("calculation")
        if type(plans) is not dict or type(calculation) is not dict:
            return False
        hard_stops = calculation.get("hard_stops")
        if type(hard_stops) is not dict:
            return False
        required_plan_fields = (
            "management_policy_id",
            "management_policy_sha256",
            "h20_date",
            "h20_exit_utc_ns",
            "expiry_safety_calendar_days",
            "invalidation_confirmation_sessions",
            "hard_stop_policy_id",
            "hard_stop_loss_ppm",
        )
        plan_values = tuple(
            plan for plan in plans.values() if type(plan) is dict
        )
        return bool(plan_values) and all(
            all(plan.get(field) is not None for field in required_plan_fields)
            and plan.get("carrier_id") in hard_stops
            for plan in plan_values
        )
    downstream_ids = (
        "ENTRY_GATE",
        "LC0_EVALUATION",
        "BCS0_EVALUATION",
        "SIZING",
        "PREFERENCE",
        "EXIT_POLICY_BINDING",
        "FINAL_DECISION",
    )
    if scenario_id == "missing_input":
        return (
            stages["INPUT_QUALIFICATION"].get("state") == "BLOCKED"
            and stages["INPUT_QUALIFICATION"].get("reason_code")
            == "CALL_UNIVERSE_INCOMPLETE"
            and all(stages[name].get("state") == "NOT_RUN" for name in downstream_ids)
        )
    if scenario_id == "policy_missing":
        return (
            stages["INPUT_QUALIFICATION"].get("state") == "BLOCKED"
            and stages["INPUT_QUALIFICATION"].get("reason_code")
            == "OWNER_APPROVED_POLICY_NOT_AVAILABLE"
            and all(stages[name].get("state") == "NOT_RUN" for name in downstream_ids)
        )
    if scenario_id == "gate_fail":
        gate = stages["ENTRY_GATE"]
        calculation = gate.get("calculation")
        if type(calculation) is not dict:
            return False
        trend = calculation.get("trend")
        breakout = calculation.get("breakout")
        return (
            gate.get("state") == "FAIL"
            and gate.get("reason_code") == "ENTRY_GATE_QUOTA_NOT_MET"
            and calculation.get("groups_must_separately_pass") is True
            and type(trend) is dict
            and trend.get("required_count") == 3
            and type(breakout) is dict
            and breakout.get("required_count") == 2
            and all(
                stages[name].get("state") == "NOT_RUN"
                for name in downstream_ids[1:]
            )
        )
    if scenario_id in {"lc_only", "bcs_only"}:
        surviving = "LC0" if scenario_id == "lc_only" else "BCS0"
        eliminated = "BCS0" if scenario_id == "lc_only" else "LC0"
        return (
            stages[f"{surviving}_EVALUATION"].get("state") == "PASS"
            and stages[f"{eliminated}_EVALUATION"].get("state") == "ELIMINATED"
            and _plan_carriers(result) == (surviving,)
            and stages["PREFERENCE"].get("reason_code")
            == "SINGLE_ELIGIBLE_STRUCTURE"
            and sizing_caps_are_minimum()
            and exit_binding_is_complete()
        )
    if scenario_id in {"both_qualified", "exact_tie"}:
        if not (
            stages["LC0_EVALUATION"].get("state") == "PASS"
            and stages["BCS0_EVALUATION"].get("state") == "PASS"
            and stages["EXIT_POLICY_BINDING"].get("reason_code")
            == "EXIT_POLICY_BOUND"
            and sizing_caps_are_minimum()
            and exit_binding_is_complete()
        ):
            return False
        preference = stages["PREFERENCE"].get("calculation")
        if type(preference) is not dict:
            return False
        structures = preference.get("structures")
        if type(structures) is not dict:
            return False
        lc = structures.get("LC0")
        bcs = structures.get("BCS0")
        if type(lc) is not dict or type(bcs) is not dict:
            return False
        if scenario_id == "both_qualified":
            return (
                _plan_carriers(result) == ("BCS0", "LC0")
                and bcs.get("comparison_numerator", 0)
                > lc.get("comparison_numerator", 0)
            )
        comparison_keys = (
            "comparison_numerator",
            "total_planned_loss_nano_usd",
            "current_entry_cash_usage_nano_usd",
        )
        return (
            _plan_carriers(result) == ("LC0", "BCS0")
            and all(lc.get(key) == bcs.get(key) for key in comparison_keys)
            and preference.get("ranking_order") == ["LC0", "BCS0"]
            and preference.get("tie_break_order", [])[-1:] == ["EXACT_TIE_LC0_FIRST"]
        )
    if scenario_id == "zero_cap":
        sizing = stages["SIZING"]
        calculation = sizing.get("calculation")
        if type(calculation) is not dict:
            return False
        structures = calculation.get("structures")
        return (
            sizing.get("state") == "ELIMINATED"
            and sizing.get("reason_code") == "ALL_STRUCTURES_ZERO_QUANTITY"
            and type(structures) is dict
            and all(
                type(value) is dict and value.get("safe_quantity") == 0
                for value in structures.values()
            )
            and sizing_caps_are_minimum()
            and all(
                stages[name].get("state") == "NOT_RUN"
                for name in ("PREFERENCE", "EXIT_POLICY_BINDING", "FINAL_DECISION")
            )
        )
    return False


def _single_artifacts(input_path: Path) -> dict[str, bytes]:
    normalized_case, result = _normalize_case(read_canonical_json_file(input_path))
    trace = _trace_document(result)
    return {
        "entry-decision-input-f0.json": canonical_json_bytes(normalized_case) + b"\n",
        "entry-decision-result-f0.json": canonical_json_bytes(result) + b"\n",
        "decision-trace-f0.json": canonical_json_bytes(trace) + b"\n",
        "final-decision-card.html": render_final_decision_card(result).encode("utf-8"),
    }


def _read_reference_assets() -> tuple[dict[str, bytes], list[dict[str, object]]]:
    artifacts: dict[str, bytes] = {}
    receipts: list[dict[str, object]] = []
    for name, path in _REFERENCE_ASSETS:
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise EntryDecisionF0Error("REFERENCE_ASSET_READ_FAILED") from exc
        if not payload:
            raise EntryDecisionF0Error("REFERENCE_ASSET_EMPTY")
        relative = f"reference/{name}"
        artifacts[relative] = payload
        receipts.append(
            {
                "asset_id": name.removesuffix(".md"),
                "path": relative,
                "sha256": sha256(payload).hexdigest(),
            }
        )
    return artifacts, receipts


def _evidence_artifacts() -> tuple[
    dict[str, bytes],
    dict[str, dict[str, object]],
]:
    artifacts: dict[str, bytes] = {}
    receipts_by_variant: dict[str, dict[str, object]] = {}
    for variant_id in ("base", "exact-tie"):
        material = synthetic_evidence_material_f0(variant_id)
        raw_input = material.get("raw_input")
        validated_bundle = material.get("validated_bundle")
        receipt_documents = material.get("receipts")
        bundle_sha256 = material.get("bundle_sha256")
        if (
            type(raw_input) is not dict
            or type(validated_bundle) is not dict
            or type(receipt_documents) is not dict
            or type(bundle_sha256) is not str
        ):
            raise EntryDecisionF0Error("ACCEPTANCE_EVIDENCE_MATERIAL_INVALID")
        prefix = f"evidence/{variant_id}/"
        input_path = prefix + "historical-structure-evidence-input-v2.json"
        bundle_path = prefix + "historical-structure-evidence-bundle-v2.json"
        artifacts[input_path] = canonical_json_bytes(raw_input) + b"\n"
        bundle_bytes = canonical_json_bytes(validated_bundle)
        artifacts[bundle_path] = bundle_bytes + b"\n"
        receipt_records: list[dict[str, object]] = []
        for carrier_id in ("LC0", "BCS0"):
            receipt = receipt_documents.get(carrier_id)
            if type(receipt) is not dict:
                raise EntryDecisionF0Error("ACCEPTANCE_EVIDENCE_RECEIPT_INVALID")
            receipt_path = prefix + f"{carrier_id.lower()}-evidence-receipt-v2.json"
            artifacts[receipt_path] = canonical_json_bytes(receipt) + b"\n"
            receipt_hash = receipt.get("evidence_sha256")
            if type(receipt_hash) is not str:
                raise EntryDecisionF0Error("ACCEPTANCE_EVIDENCE_RECEIPT_INVALID")
            receipt_records.append(
                {
                    "carrier_id": carrier_id,
                    "path": receipt_path,
                    "evidence_sha256": receipt_hash,
                }
            )
        receipts_by_variant[variant_id] = {
            "variant_id": variant_id,
            "input_path": input_path,
            "input_sha256": canonical_json_sha256(raw_input),
            "bundle_path": bundle_path,
            "bundle_sha256": bundle_sha256,
            "receipts": receipt_records,
        }
    return artifacts, receipts_by_variant


def _determinism_snapshot_for(
    normalized_case: dict[str, object],
    result: dict[str, object],
) -> dict[str, object]:
    trace = _trace_document(result)
    input_bytes = canonical_json_bytes(normalized_case)
    result_bytes = canonical_json_bytes(result)
    trace_bytes = canonical_json_bytes(trace)
    card_bytes = render_final_decision_card(result).encode("utf-8")
    return {
        "input_bytes_sha256": sha256(input_bytes).hexdigest(),
        "input_bytes_length": len(input_bytes),
        "result_bytes_sha256": sha256(result_bytes).hexdigest(),
        "result_bytes_length": len(result_bytes),
        "trace_bytes_sha256": sha256(trace_bytes).hexdigest(),
        "trace_bytes_length": len(trace_bytes),
        "card_bytes_sha256": sha256(card_bytes).hexdigest(),
        "card_bytes_length": len(card_bytes),
    }


def _permuted_input_order_case(document: dict[str, object]) -> dict[str, object]:
    permuted = deepcopy(document)
    entry_input = permuted.get("entry_input")
    if type(entry_input) is not dict:
        raise EntryDecisionF0Error("DETERMINISM_FIXTURE_INVALID")
    for key in ("call_universe", "lc0_economics", "bcs0_economics"):
        values = entry_input.get(key)
        if type(values) is not list:
            raise EntryDecisionF0Error("DETERMINISM_FIXTURE_INVALID")
        values.reverse()
    return permuted


def _determinism_record_for_case(document: dict[str, object]) -> dict[str, object]:
    normalized_case, result = _normalize_case(document)
    permuted_case, permuted_result = _normalize_case(
        _permuted_input_order_case(document)
    )
    baseline = _determinism_snapshot_for(normalized_case, result)
    permuted_input_order = _determinism_snapshot_for(
        permuted_case,
        permuted_result,
    )
    return {
        "baseline": baseline,
        "permuted_input_order": permuted_input_order,
        "within_process_permutation_identical": baseline == permuted_input_order,
    }


def _determinism_snapshot_local() -> dict[str, object]:
    snapshots: dict[str, object] = {}
    for specification in ACCEPTANCE_SCENARIOS_F0:
        scenario_id = str(specification["scenario_id"])
        snapshots[scenario_id] = _determinism_record_for_case(
            build_synthetic_entry_decision_case_f0(scenario_id)
        )
    return snapshots


def _determinism_snapshot_cross_process() -> dict[str, object]:
    source = (
        "import sys\n"
        "from gld_entry_decision_f0.canonical import canonical_json_bytes\n"
        "from gld_entry_decision_f0.cli import _determinism_snapshot_local\n"
        "sys.stdout.buffer.write(canonical_json_bytes(_determinism_snapshot_local()))\n"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", source],
            cwd=_PROJECT_ROOT,
            env={"PYTHONPATH": str(_PROJECT_ROOT / "src")},
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EntryDecisionF0Error("DETERMINISM_CROSS_PROCESS_FAILED") from exc
    if completed.returncode != 0 or completed.stderr:
        raise EntryDecisionF0Error("DETERMINISM_CROSS_PROCESS_FAILED")
    try:
        document = json.loads(completed.stdout)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise EntryDecisionF0Error("DETERMINISM_CROSS_PROCESS_INVALID") from exc
    if type(document) is not dict or canonical_json_bytes(document) != completed.stdout:
        raise EntryDecisionF0Error("DETERMINISM_CROSS_PROCESS_INVALID")
    return document


def _acceptance_artifacts() -> dict[str, bytes]:
    artifacts, reference_receipts = _read_reference_assets()
    evidence_artifacts, evidence_variants = _evidence_artifacts()
    artifacts.update(evidence_artifacts)
    cross_process_snapshots = _determinism_snapshot_cross_process()
    root_prefix = ""
    rows: list[dict[str, object]] = []
    manifest_scenarios: list[dict[str, object]] = []
    mismatch = False
    for specification in ACCEPTANCE_SCENARIOS_F0:
        scenario_id = str(specification["scenario_id"])
        raw_case = build_synthetic_entry_decision_case_f0(scenario_id)
        evidence_variant_id = "exact-tie" if scenario_id == "exact_tie" else "base"
        evidence_receipt = evidence_variants[evidence_variant_id]
        normalized_case, result = _normalize_case(raw_case)
        trace = _trace_document(result)
        local_determinism = _determinism_record_for_case(raw_case)
        cross_process_determinism = cross_process_snapshots.get(scenario_id)
        current_snapshot = _determinism_snapshot_for(normalized_case, result)
        determinism_pass = (
            local_determinism == cross_process_determinism
            and local_determinism.get("within_process_permutation_identical") is True
            and local_determinism.get("baseline") == current_snapshot
        )
        actual_status = result.get("decision_status")
        actual_carriers = _plan_carriers(result)
        expected_status = specification["expected_status"]
        expected_carriers = specification["expected_carriers"]
        passed = (
            actual_status == expected_status
            and actual_carriers == expected_carriers
            and _result_evidence_valid(result)
            and _scenario_business_evidence(scenario_id, result)
            and determinism_pass
        )
        mismatch = mismatch or not passed
        evidence_status = (
            "AUTOMATED_EVIDENCE_PASS" if passed else "AUTOMATED_EVIDENCE_FAIL"
        )
        input_relative = f"{scenario_id}/entry-decision-input-f0.json"
        result_relative = f"{scenario_id}/entry-decision-result-f0.json"
        trace_relative = f"{scenario_id}/decision-trace-f0.json"
        card_relative = f"{scenario_id}/final-decision-card.html"
        input_payload = canonical_json_bytes(normalized_case) + b"\n"
        result_payload = canonical_json_bytes(result) + b"\n"
        trace_payload = canonical_json_bytes(trace) + b"\n"
        card_payload = render_final_decision_card(result).encode("utf-8")
        artifacts[root_prefix + input_relative] = input_payload
        artifacts[root_prefix + result_relative] = result_payload
        artifacts[root_prefix + trace_relative] = trace_payload
        artifacts[root_prefix + card_relative] = card_payload
        trace_hash = trace.get("trace_sha256")
        result_hash = result.get("result_sha256")
        if type(trace_hash) is not str or type(result_hash) is not str:
            raise EntryDecisionF0Error("ACCEPTANCE_HASH_INVALID")
        row = {
            "scenario_id": scenario_id,
            "owner_label": specification["owner_label"],
            "actual_zh": _actual_text_zh(result),
            "purpose": specification["purpose"],
            "key_input": specification["key_input_zh"],
            "expected": _expected_text(specification),
            "actual": _actual_text(result),
            "automated_evidence_status": evidence_status,
            "owner_status": "OWNER_ACCEPTANCE_PENDING",
            "input_sha256": canonical_json_sha256(normalized_case),
            "trace_sha256": trace_hash,
            "result_sha256": result_hash,
            "card_sha256": sha256(card_payload).hexdigest(),
            "evidence_variant_id": evidence_variant_id,
            "evidence_bundle_sha256": evidence_receipt["bundle_sha256"],
            "evidence_bundle_href": str(evidence_receipt["bundle_path"]).removeprefix(
                root_prefix
            ),
            "input_href": input_relative,
            "result_href": result_relative,
            "trace_href": trace_relative,
            "card_href": card_relative,
        }
        rows.append(row)
        manifest_scenarios.append(
            {
                "scenario_id": scenario_id,
                "purpose": specification["purpose"],
                "expected": {
                    "decision_status": expected_status,
                    "carrier_order": list(expected_carriers),
                    "preferred_carrier": specification["expected_preferred_carrier"],
                },
                "actual": {
                    "decision_status": actual_status,
                    "carrier_order": list(actual_carriers),
                    "reason_code": result.get("reason_code"),
                },
                "automated_evidence_status": evidence_status,
                "owner_status": "OWNER_ACCEPTANCE_PENDING",
                "input_sha256": row["input_sha256"],
                "trace_sha256": trace_hash,
                "result_sha256": result_hash,
                "artifact_hashes": {
                    "input_file_sha256": sha256(input_payload).hexdigest(),
                    "result_file_sha256": sha256(result_payload).hexdigest(),
                    "trace_file_sha256": sha256(trace_payload).hexdigest(),
                    "card_file_sha256": sha256(card_payload).hexdigest(),
                },
                "determinism_receipt": {
                    "status": (
                        "CROSS_PROCESS_BYTE_IDENTICAL"
                        if determinism_pass
                        else "CROSS_PROCESS_MISMATCH"
                    ),
                    "input_order_status": (
                        "NORMALIZED_INPUT_ORDER_BYTE_IDENTICAL"
                        if determinism_pass
                        else "NORMALIZED_INPUT_ORDER_MISMATCH"
                    ),
                    "local": local_determinism,
                    "independent_process": cross_process_determinism,
                },
                "evidence_variant_id": evidence_variant_id,
                "evidence_bundle_sha256": evidence_receipt["bundle_sha256"],
                "input_path": root_prefix + input_relative,
                "result_path": root_prefix + result_relative,
                "trace_path": root_prefix + trace_relative,
                "card_path": root_prefix + card_relative,
            }
        )
    if mismatch:
        raise EntryDecisionF0Error("ACCEPTANCE_SCENARIO_MISMATCH")
    owner_payload = render_owner_acceptance_index(rows).encode("utf-8")
    engineering_payload = render_engineering_evidence(rows).encode("utf-8")
    manifest_body = {
        "schema_version": "GLD_ENTRY_DECISION_ACCEPTANCE_MANIFEST_F0_V1",
        "classification": "RESEARCH_ONLY",
        "authority_status": "NO_DECISION_EFFECT",
        "actionable": False,
        "broker_order_count": 0,
        "owner_status": "OWNER_ACCEPTANCE_PENDING",
        "reference_assets": reference_receipts,
        "evidence_variants": [
            evidence_variants[variant_id]
            for variant_id in ("base", "exact-tie")
        ],
        "rendered_artifacts": [
            {
                "path": root_prefix + "index.html",
                "sha256": sha256(owner_payload).hexdigest(),
            },
            {
                "path": root_prefix + "engineering-evidence.html",
                "sha256": sha256(engineering_payload).hexdigest(),
            },
        ],
        "scenarios": manifest_scenarios,
    }
    manifest = {
        **manifest_body,
        "manifest_sha256": canonical_json_sha256(manifest_body),
    }
    artifacts["acceptance-manifest.json"] = canonical_json_bytes(manifest) + b"\n"
    artifacts[root_prefix + "index.html"] = owner_payload
    artifacts[root_prefix + "engineering-evidence.html"] = engineering_payload
    return artifacts


def _failure_receipt(reason_code: str) -> dict[str, object]:
    body = {
        "schema_version": "ENTRY_DECISION_RUN_RECEIPT_F0_V1",
        "classification": "RESEARCH_ONLY",
        "authority_status": "NO_DECISION_EFFECT",
        "run_status": "RUN_FAILED",
        "reason_code": reason_code,
        "decision_result_generated": False,
        "actionable": False,
        "broker_order_count": 0,
    }
    return {**body, "receipt_sha256": canonical_json_sha256(body)}


def _publish_failure_receipt(output: Path, reason_code: str) -> None:
    receipt = _failure_receipt(reason_code)
    publish_artifacts(
        output,
        {"run-receipt.json": canonical_json_bytes(receipt) + b"\n"},
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_gld_entry_decision_f0",
        description="Run deterministic, non-actionable GLD Entry Decision Research F0.",
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--demo-acceptance", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.demo_acceptance:
        if arguments.input is not None:
            parser.error("--demo-acceptance cannot be combined with --input")
    elif arguments.input is None:
        parser.error("single mode requires --input")
    try:
        artifacts = (
            _acceptance_artifacts()
            if arguments.demo_acceptance
            else _single_artifacts(arguments.input)
        )
        publish_artifacts(arguments.output, artifacts)
    except EntryDecisionF0Error as exc:
        reason_code = exc.reason_code
        try:
            _publish_failure_receipt(arguments.output, reason_code)
        except EntryDecisionF0Error:
            pass
        print(f"ERROR {reason_code}", file=sys.stderr)
        return 2
    except Exception as exc:
        reason_code = "UNEXPECTED_LOCAL_ERROR"
        try:
            _publish_failure_receipt(arguments.output, reason_code)
        except EntryDecisionF0Error:
            pass
        print(f"ERROR {reason_code} {type(exc).__name__}", file=sys.stderr)
        return 2
    print(str(arguments.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
