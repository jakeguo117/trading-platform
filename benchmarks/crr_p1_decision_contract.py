"""Additive backend-neutral Decision semantic goldens for CRR P1.

The sealed documents in this module are derived only from the already-frozen
typed U64/W64 corpora and reference Call terminal goldens.  They deliberately
contain no native execution provenance and do not execute the production BCS
or Decision layer.  U64 is a future formal end-to-end semantic target; W64 is
warmup conformance only and can never authorize a formal timed receipt.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence

from gld_research_core.crr_delta import MODEL_SHA256
from gld_research_core.facts import (
    QUOTE_QUALITY_POLICY_SHA256,
    QUOTE_QUALITY_POLICY_VERSION,
    OptionQuoteV1,
    build_option_snapshot_candidate_ledger,
)

from .crr_p1_contract import (
    FIXTURE_COUNT,
    STATIC_MANIFEST_SHA256,
    BenchmarkContractError,
    LoadedCorpusV1,
    canonical_json_bytes,
    load_corpus,
    load_reference_golden,
    load_static_manifest,
)


DECISION_GOLDEN_SCHEMA_VERSION = "GLD_CRR_P1_DECISION_SEMANTIC_GOLDEN_V1"
DECISION_STATIC_MANIFEST_SCHEMA_VERSION = (
    "GLD_CRR_P1_DECISION_STATIC_MANIFEST_V1"
)
BACKEND_EVIDENCE_SCHEMA_VERSION = (
    "GLD_CRR_P1_DECISION_BACKEND_EVIDENCE_ENVELOPE_V1"
)
FREEZE_STATUS = (
    "LATE_ADDITIVE_FREEZE_AFTER_NATIVE_PRICER_BEFORE_NATIVE_DECISION_LAYER"
)
FORMAL_ROLE = "FORMAL_E2E_SEMANTIC"
WARMUP_ROLE = "UNTIMED_WARMUP_CONFORMANCE"
RESEARCH_CONTRACT_SHA256 = (
    "3940c27b4aec3585b9aa904a96f2118178589fafdcee46412441431ff9fcb8fc"
)
P1_CONTRACT_SHA256 = (
    "8191b769ecb222557eb0770f7e64eaf8bbcb35bf4a0d836ab91e52cc17c445d5"
)
QUANTITY = 1

# Filled after the offline one-shot generator seals both Decision goldens and
# their additive manifest.  Changing the manifest is therefore a source edit.
DECISION_STATIC_MANIFEST_SHA256 = (
    "b001d080f15d3d65734c2bfecc3bc55c43709a7f181e0dae2f9868bc40f528f0"
)

_SHORT_DELTA_MIN_PPM = 200_000
_SHORT_DELTA_MAX_PPM = 300_000
_SHORT_DELTA_TARGET_PPM = 250_000

_ROOT = Path(__file__).resolve().parents[1]
_BENCHMARK_ROOT = Path(__file__).resolve().parent
_FIXTURE_ROOT = _BENCHMARK_ROOT / "fixtures"
_DECISION_STATIC_MANIFEST_PATH = (
    _FIXTURE_ROOT / "decision_static_manifest_v0.1.json"
)
_RESEARCH_CONTRACT_PATH = (
    _ROOT / "2026-08-28-1515-gld-research-contract-v0.2.md"
)
_P1_CONTRACT_PATH = (
    _ROOT / "2026-08-28-2046-gld-crr-p1-benchmark-contract-v0.1.md"
)

_GOLDEN_PATHS = {
    "U64": "fixtures/u64_decision_semantic_golden_v0.1.json",
    "W64": "fixtures/w64_decision_semantic_golden_v0.1.json",
}
_SOURCE_CALL_GOLDEN_PATHS = {
    "U64": "fixtures/u64_reference_golden_v0.1.json",
    "W64": "fixtures/w64_reference_golden_v0.1.json",
}
_SOURCE_FIXTURE_PATHS = {
    "U64": "fixtures/u64_v0.1.json",
    "W64": "fixtures/w64_v0.1.json",
}
_ROLE_BY_FIXTURE = {"U64": FORMAL_ROLE, "W64": WARMUP_ROLE}
_EXPECTED_PROJECTION_FACTS = {
    "U64": {
        "long_call_id": "GLD   270226C00200000",
        "short_call_id": "GLD   270226C00236000",
        "long_delta_ppm": 554_798,
        "short_delta_ppm": 260_543,
        "net_delta_ppm": 294_255,
        "base_net_debit_nano_usd": 1_153_300_000_000,
        "stressed_net_debit_nano_usd": 1_155_300_000_000,
        "gross_expiry_width_value_nano_usd": 3_600_000_000_000,
        "theoretical_expiry_max_profit_nano_usd": 2_446_700_000_000,
    },
    "W64": {
        "long_call_id": "GLD   270226C00200000",
        "short_call_id": "GLD   270226C00250000",
        "long_delta_ppm": 601_101,
        "short_delta_ppm": 254_075,
        "net_delta_ppm": 347_026,
        "base_net_debit_nano_usd": 1_627_300_000_000,
        "stressed_net_debit_nano_usd": 1_629_300_000_000,
        "gross_expiry_width_value_nano_usd": 5_000_000_000_000,
        "theoretical_expiry_max_profit_nano_usd": 3_372_700_000_000,
    },
}

_SEMANTIC_BANNED_KEYS = frozenset(
    {
        "artifact_sha256",
        "backend_artifact_sha256",
        "backend_evidence_sha256",
        "backend_execution_sha256",
        "backend_input_sha256",
        "bcs_selection_sha256",
        "evaluation_sha256",
        "long_delta_evidence_sha256",
        "long_delta_run_sha256",
        "long_delta_runtime_fingerprint_sha256",
        "native_provenance_sha256",
        "result_sha256",
        "selection_sha256",
        "short_delta_evidence_sha256",
        "short_delta_run_sha256",
        "short_delta_runtime_fingerprint_sha256",
    }
)


__all__ = [
    "BACKEND_EVIDENCE_SCHEMA_VERSION",
    "DECISION_GOLDEN_SCHEMA_VERSION",
    "DECISION_STATIC_MANIFEST_SCHEMA_VERSION",
    "DECISION_STATIC_MANIFEST_SHA256",
    "FORMAL_ROLE",
    "FREEZE_STATUS",
    "P1_CONTRACT_SHA256",
    "QUANTITY",
    "RESEARCH_CONTRACT_SHA256",
    "WARMUP_ROLE",
    "BackendEvidenceEnvelopeV1",
    "DecisionContractError",
    "LoadedDecisionSemanticGoldenV1",
    "assert_formal_timed_receipt_permitted",
    "derive_decision_semantic_document",
    "load_decision_semantic_golden",
    "load_decision_static_manifest",
    "semantic_document_sha256",
    "validate_backend_evidence_envelope",
]


class DecisionContractError(ValueError):
    """One deterministic Decision semantic-contract failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _require_sha256(value: object, reason_code: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise DecisionContractError(reason_code)
    return value


def _require_exact_keys(
    value: object,
    expected: set[str],
    reason_code: str,
) -> dict[str, object]:
    if type(value) is not dict or set(value) != expected:
        raise DecisionContractError(reason_code)
    return value


def _freeze_json(value: object) -> object:
    if type(value) is dict:
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if type(value) is list:
        return tuple(_freeze_json(item) for item in value)
    return value


def _exact_json_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(
            _exact_json_equal(left[key], right[key]) for key in left
        )
    if type(left) is list:
        return len(left) == len(right) and all(
            _exact_json_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def _read_canonical_json(path: Path) -> tuple[bytes, dict[str, object]]:
    try:
        raw = path.read_bytes()
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise DecisionContractError(
            "DECISION_STATIC_ARTIFACT_INVALID"
        ) from error
    if type(document) is not dict or raw != canonical_json_bytes(document):
        raise DecisionContractError("DECISION_CANONICAL_JSON_INVALID")
    return raw, document


def _verify_contract_file(path: Path, expected_sha256: str) -> None:
    try:
        actual = sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise DecisionContractError(
            "DECISION_CONTRACT_ARTIFACT_INVALID"
        ) from error
    if actual != expected_sha256:
        raise DecisionContractError("DECISION_CONTRACT_HASH_MISMATCH")


def _walk_semantic_keys(value: object) -> tuple[str, ...]:
    keys: list[str] = []
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise DecisionContractError("DECISION_SEMANTIC_SCHEMA_INVALID")
            keys.append(key)
            keys.extend(_walk_semantic_keys(item))
    elif type(value) is list:
        for item in value:
            keys.extend(_walk_semantic_keys(item))
    return tuple(keys)


def _assert_semantic_boundary(document: object) -> None:
    for key in _walk_semantic_keys(document):
        if (
            key in _SEMANTIC_BANNED_KEYS
            or key.startswith(("backend_", "native_"))
            or key.endswith(
                (
                    "_evidence_sha256",
                    "_run_sha256",
                    "_runtime_fingerprint_sha256",
                )
            )
        ):
            raise DecisionContractError(
                "DECISION_BACKEND_PROVENANCE_FORBIDDEN"
            )


def semantic_document_sha256(document: object) -> str:
    """Hash a canonical semantic document, excluding only its own hash."""

    if type(document) is not dict:
        raise DecisionContractError("DECISION_SEMANTIC_SCHEMA_INVALID")
    payload = {
        key: value
        for key, value in document.items()
        if key != "decision_semantic_sha256"
    }
    try:
        return sha256(canonical_json_bytes(payload)).hexdigest()
    except BenchmarkContractError as error:
        raise DecisionContractError(
            "DECISION_CANONICAL_JSON_INVALID"
        ) from error


@dataclass(frozen=True, slots=True)
class BackendEvidenceEnvelopeV1:
    """Future backend evidence binding, intentionally outside semantic goldens."""

    schema_version: str
    fixture_id: str
    semantic_sha256: str
    backend_evidence_sha256: str
    input_sha256: str
    execution_sha256: str
    artifact_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not str
            or self.schema_version != BACKEND_EVIDENCE_SCHEMA_VERSION
            or type(self.fixture_id) is not str
            or self.fixture_id not in _ROLE_BY_FIXTURE
        ):
            raise DecisionContractError("DECISION_BACKEND_ENVELOPE_INVALID")
        for value in (
            self.semantic_sha256,
            self.backend_evidence_sha256,
            self.input_sha256,
            self.execution_sha256,
            self.artifact_sha256,
        ):
            _require_sha256(value, "DECISION_BACKEND_ENVELOPE_INVALID")


def validate_backend_evidence_envelope(
    document: object,
) -> BackendEvidenceEnvelopeV1:
    values = _require_exact_keys(
        document,
        {
            "artifact_sha256",
            "backend_evidence_sha256",
            "execution_sha256",
            "fixture_id",
            "input_sha256",
            "schema_version",
            "semantic_sha256",
        },
        "DECISION_BACKEND_ENVELOPE_INVALID",
    )
    try:
        envelope = BackendEvidenceEnvelopeV1(**values)  # type: ignore[arg-type]
    except TypeError as error:
        raise DecisionContractError(
            "DECISION_BACKEND_ENVELOPE_INVALID"
        ) from error
    semantic_golden = load_decision_semantic_golden(envelope.fixture_id)
    if envelope.semantic_sha256 != semantic_golden.decision_semantic_sha256:
        raise DecisionContractError("DECISION_BACKEND_ENVELOPE_LINEAGE_INVALID")
    return envelope


@dataclass(frozen=True, slots=True)
class LoadedDecisionSemanticGoldenV1:
    fixture_id: str
    role: str
    formal_timed_receipt_permitted: bool
    document: Mapping[str, object]
    decision_semantic_sha256: str
    projection_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.fixture_id) is not str
            or self.fixture_id not in _ROLE_BY_FIXTURE
            or type(self.role) is not str
            or self.role != _ROLE_BY_FIXTURE[self.fixture_id]
            or type(self.formal_timed_receipt_permitted) is not bool
            or self.formal_timed_receipt_permitted
            != (self.fixture_id == "U64")
            or not isinstance(self.document, Mapping)
        ):
            raise DecisionContractError("DECISION_GOLDEN_SCHEMA_INVALID")
        _require_sha256(
            self.decision_semantic_sha256,
            "DECISION_SEMANTIC_HASH_INVALID",
        )
        _require_sha256(
            self.projection_sha256,
            "DECISION_PROJECTION_HASH_INVALID",
        )


def _quote_by_id(corpus: LoadedCorpusV1) -> dict[str, OptionQuoteV1]:
    return {
        quote.contract.occ_symbol: quote
        for quote in corpus.snapshot.option_quotes
    }


def _pair_is_eligible(
    long_quote: OptionQuoteV1,
    short_quote: OptionQuoteV1,
) -> bool:
    long_contract = long_quote.contract
    short_contract = short_quote.contract
    return (
        short_contract.right == "C"
        and short_contract.exercise_style == "AMERICAN"
        and long_contract.exercise_style == "AMERICAN"
        and short_contract.expiry_utc_ns == long_contract.expiry_utc_ns
        and short_contract.strike_nano_usd > long_contract.strike_nano_usd
        and short_contract.multiplier == long_contract.multiplier
        and short_contract.deliverable == long_contract.deliverable
        and short_contract.currency == long_contract.currency
        and short_contract.standard_unadjusted is True
        and long_contract.standard_unadjusted is True
    )


def _select_short_contract_id(
    *,
    corpus: LoadedCorpusV1,
    terminal_documents: Sequence[Mapping[str, object]],
    suite: str,
) -> str:
    if suite not in {"coarse", "fine"}:
        raise DecisionContractError("DECISION_SELECTOR_SUITE_INVALID")
    quote_by_id = _quote_by_id(corpus)
    terminal_by_id = {
        terminal["contract_id"]: terminal for terminal in terminal_documents
    }
    long_quote = quote_by_id.get(corpus.lc0_binding.long_call_id)
    long_terminal = terminal_by_id.get(corpus.lc0_binding.long_call_id)
    delta_field = f"{suite}_delta_ppm"
    if long_quote is None or long_terminal is None:
        raise DecisionContractError("DECISION_LONG_CALL_BINDING_INVALID")
    long_delta = long_terminal.get(delta_field)
    if (
        long_terminal.get("terminal_status") != "PASS"
        or type(long_delta) is not int
    ):
        raise DecisionContractError("DECISION_LONG_CALL_TERMINAL_INVALID")

    eligible: list[tuple[int, int, str]] = []
    for contract_id, quote in quote_by_id.items():
        if contract_id == corpus.lc0_binding.long_call_id:
            continue
        terminal = terminal_by_id.get(contract_id)
        if terminal is None:
            raise DecisionContractError("DECISION_CALL_TERMINAL_BINDING_INVALID")
        delta = terminal.get(delta_field)
        if (
            terminal.get("terminal_status") == "PASS"
            and type(delta) is int
            and _pair_is_eligible(long_quote, quote)
            and _SHORT_DELTA_MIN_PPM <= delta <= _SHORT_DELTA_MAX_PPM
            and delta < long_delta
        ):
            eligible.append(
                (
                    abs(delta - _SHORT_DELTA_TARGET_PPM),
                    -quote.contract.strike_nano_usd,
                    contract_id,
                )
            )
    if not eligible:
        raise DecisionContractError("DECISION_NO_ELIGIBLE_SHORT_CALL")
    return min(eligible)[2]


def _entry_quote_projection(
    quote: OptionQuoteV1,
    *,
    side: str,
) -> dict[str, object]:
    book = quote.top_of_book
    if side == "ASK":
        price = book.ask_nano_usd
        displayed_size = book.ask_size
    elif side == "BID":
        price = book.bid_nano_usd
        displayed_size = book.bid_size
    else:
        raise DecisionContractError("DECISION_ENTRY_QUOTE_SIDE_INVALID")
    if displayed_size < QUANTITY:
        raise DecisionContractError("DECISION_ENTRY_QUOTE_SIZE_INVALID")
    return {
        "contract_id": quote.contract.occ_symbol,
        "displayed_size": displayed_size,
        "price_nano_usd": price,
        "side": side,
        "tick_nano_usd": book.tick_nano_usd,
    }


def _derive_projection(
    *,
    fixture_id: str,
    corpus: LoadedCorpusV1,
    terminal_documents: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    coarse_winner = _select_short_contract_id(
        corpus=corpus,
        terminal_documents=terminal_documents,
        suite="coarse",
    )
    fine_winner = _select_short_contract_id(
        corpus=corpus,
        terminal_documents=terminal_documents,
        suite="fine",
    )
    if coarse_winner != fine_winner:
        raise DecisionContractError("DECISION_SELECTOR_MODEL_INSTABILITY")

    quote_by_id = _quote_by_id(corpus)
    terminal_by_id = {
        terminal["contract_id"]: terminal for terminal in terminal_documents
    }
    long_id = corpus.lc0_binding.long_call_id
    long_quote = quote_by_id[long_id]
    short_quote = quote_by_id[fine_winner]
    long_terminal = terminal_by_id[long_id]
    short_terminal = terminal_by_id[fine_winner]
    long_delta = long_terminal["fine_delta_ppm"]
    short_delta = short_terminal["fine_delta_ppm"]
    long_coarse_delta = long_terminal["coarse_delta_ppm"]
    short_coarse_delta = short_terminal["coarse_delta_ppm"]
    if any(
        type(value) is not int
        for value in (
            long_delta,
            short_delta,
            long_coarse_delta,
            short_coarse_delta,
        )
    ):
        raise DecisionContractError("DECISION_DELTA_TYPE_INVALID")
    net_delta = long_delta - short_delta

    long_entry_quote = _entry_quote_projection(long_quote, side="ASK")
    short_entry_quote = _entry_quote_projection(short_quote, side="BID")
    long_entry_fee = corpus.fees.long_entry_fee_nano_usd_per_contract
    short_entry_fee = corpus.fees.short_entry_fee_nano_usd_per_contract
    total_entry_fees = (long_entry_fee + short_entry_fee) * QUANTITY
    multiplier_quantity = long_quote.contract.multiplier * QUANTITY
    base_debit = (
        long_entry_quote["price_nano_usd"]
        - short_entry_quote["price_nano_usd"]
    ) * multiplier_quantity + total_entry_fees
    stressed_short_bid = max(
        0,
        short_entry_quote["price_nano_usd"]
        - short_entry_quote["tick_nano_usd"],
    )
    stressed_debit = (
        long_entry_quote["price_nano_usd"]
        + long_entry_quote["tick_nano_usd"]
        - stressed_short_bid
    ) * multiplier_quantity + total_entry_fees
    gross_width = (
        short_quote.contract.strike_nano_usd
        - long_quote.contract.strike_nano_usd
    ) * multiplier_quantity
    expiry_cap = gross_width - base_debit
    if not 0 < base_debit <= stressed_debit < gross_width:
        raise DecisionContractError("DECISION_ENTRY_COST_INVALID")

    actual_facts = {
        "base_net_debit_nano_usd": base_debit,
        "gross_expiry_width_value_nano_usd": gross_width,
        "long_call_id": long_id,
        "long_delta_ppm": long_delta,
        "net_delta_ppm": net_delta,
        "short_call_id": fine_winner,
        "short_delta_ppm": short_delta,
        "stressed_net_debit_nano_usd": stressed_debit,
        "theoretical_expiry_max_profit_nano_usd": expiry_cap,
    }
    if actual_facts != _EXPECTED_PROJECTION_FACTS[fixture_id]:
        raise DecisionContractError("DECISION_FROZEN_PROJECTION_MISMATCH")

    return {
        "carrier_terminals": [
            {
                "carrier_id": "LC0",
                "reason_code": "LC0_AUTHORITY_NOT_IMPLEMENTED",
                "terminal_status": "NO_DECISION",
            },
            {
                "carrier_id": "BCS0",
                "reason_code": "RESEARCH_ONLY_NOT_ACTIONABLE",
                "terminal_status": "PASS",
            },
        ],
        "decision": {
            "actionable": False,
            "broker_order_count": 0,
            "owner_selection_required": False,
            "reason_code": "CARRIER_EVALUATION_INCOMPLETE",
            "status": "NO_DECISION",
        },
        "entry_cost": {
            "base_net_debit_nano_usd": base_debit,
            "gross_expiry_width_value_nano_usd": gross_width,
            "max_loss_nano_usd": base_debit,
            "quantity": QUANTITY,
            "stressed_net_debit_nano_usd": stressed_debit,
            "theoretical_expiry_max_profit_nano_usd": expiry_cap,
            "theoretical_expiry_only": True,
        },
        "entry_fees": {
            "long_entry_fee_nano_usd_per_contract": long_entry_fee,
            "short_entry_fee_nano_usd_per_contract": short_entry_fee,
            "total_entry_fees_nano_usd": total_entry_fees,
        },
        "entry_quotes": {
            "long": long_entry_quote,
            "short": short_entry_quote,
        },
        "selection": {
            "expiry_utc_ns": long_quote.contract.expiry_utc_ns,
            "long_call_id": long_id,
            "long_coarse_delta_ppm": long_coarse_delta,
            "long_delta_ppm": long_delta,
            "long_strike_nano_usd": long_quote.contract.strike_nano_usd,
            "multiplier": long_quote.contract.multiplier,
            "net_delta_ppm": net_delta,
            "short_call_id": fine_winner,
            "short_coarse_delta_ppm": short_coarse_delta,
            "short_delta_ppm": short_delta,
            "short_strike_nano_usd": short_quote.contract.strike_nano_usd,
        },
        "selector_policy": {
            "coarse_fine_winner_must_match": True,
            "short_delta_max_ppm": _SHORT_DELTA_MAX_PPM,
            "short_delta_min_ppm": _SHORT_DELTA_MIN_PPM,
            "short_delta_target_ppm": _SHORT_DELTA_TARGET_PPM,
            "winner_key_order": [
                "absolute_delta_distance_to_target",
                "higher_strike",
                "contract_id_lexical",
            ],
        },
    }


def _source_file_sha256(relative_path: str) -> str:
    manifest = load_static_manifest()
    files = manifest.get("files")
    if type(files) is not dict:
        raise DecisionContractError("DECISION_SOURCE_MANIFEST_INVALID")
    value = files.get(relative_path)
    return _require_sha256(value, "DECISION_SOURCE_MANIFEST_INVALID")


def derive_decision_semantic_document(fixture_id: str) -> dict[str, object]:
    """Derive one backend-neutral semantic golden from frozen source facts."""

    if type(fixture_id) is not str or fixture_id not in _ROLE_BY_FIXTURE:
        raise DecisionContractError("DECISION_FIXTURE_ID_INVALID")
    _verify_contract_file(_RESEARCH_CONTRACT_PATH, RESEARCH_CONTRACT_SHA256)
    _verify_contract_file(_P1_CONTRACT_PATH, P1_CONTRACT_SHA256)
    corpus = load_corpus(fixture_id)
    source_golden = load_reference_golden(fixture_id)
    terminal_documents = deepcopy(source_golden["results"])
    if type(terminal_documents) is not list or len(terminal_documents) != FIXTURE_COUNT:
        raise DecisionContractError("DECISION_CALL_TERMINAL_COUNT_INVALID")
    call_semantic_sha256 = sha256(
        canonical_json_bytes(terminal_documents)
    ).hexdigest()
    if call_semantic_sha256 != source_golden["semantic_output_sha256"]:
        raise DecisionContractError("DECISION_CALL_SEMANTIC_HASH_MISMATCH")
    candidate_ledger = build_option_snapshot_candidate_ledger(
        (corpus.snapshot,),
        signal_snapshot=corpus.signal,
    )
    projection = _derive_projection(
        fixture_id=fixture_id,
        corpus=corpus,
        terminal_documents=terminal_documents,
    )
    declared_hashes = corpus.document.get("declared_hashes")
    if not isinstance(declared_hashes, Mapping):
        raise DecisionContractError("DECISION_CORPUS_LINEAGE_INVALID")
    body: dict[str, object] = {
        "call_semantic_output_sha256": call_semantic_sha256,
        "call_terminals": terminal_documents,
        "decision_projection": projection,
        "fixture_id": fixture_id,
        "formal_timed_receipt_permitted": fixture_id == "U64",
        "freeze_status": FREEZE_STATUS,
        "lineage": {
            "call_golden_file_sha256": _source_file_sha256(
                _SOURCE_CALL_GOLDEN_PATHS[fixture_id]
            ),
            "candidate_ledger_sha256": candidate_ledger.ledger_sha256,
            "corpus_semantic_sha256": declared_hashes[
                "corpus_semantic_sha256"
            ],
            "delta_model_sha256": MODEL_SHA256,
            "fee_schedule_sha256": corpus.fees.fee_schedule_sha256,
            "fixture_file_sha256": _source_file_sha256(
                _SOURCE_FIXTURE_PATHS[fixture_id]
            ),
            "input_vector_sha256": corpus.input_vector_sha256,
            "lc0_binding_sha256": corpus.lc0_binding.binding_sha256,
            "option_snapshot_sha256": corpus.snapshot.snapshot_sha256,
            "p1_contract_sha256": P1_CONTRACT_SHA256,
            "quote_quality_policy_sha256": QUOTE_QUALITY_POLICY_SHA256,
            "quote_quality_policy_version": QUOTE_QUALITY_POLICY_VERSION,
            "research_contract_sha256": RESEARCH_CONTRACT_SHA256,
            "rule_package_version": corpus.signal.rule_version,
            "rule_sha256": corpus.signal.rule_sha256,
            "signal_snapshot_sha256": corpus.signal.snapshot_sha256,
            "source_static_manifest_sha256": STATIC_MANIFEST_SHA256,
        },
        "quantity": QUANTITY,
        "role": _ROLE_BY_FIXTURE[fixture_id],
        "schema_version": DECISION_GOLDEN_SCHEMA_VERSION,
    }
    _assert_semantic_boundary(body)
    body["decision_semantic_sha256"] = semantic_document_sha256(body)
    return body


def _build_decision_static_manifest_document(
    file_hashes: Mapping[str, str],
) -> dict[str, object]:
    if (
        type(file_hashes) is not dict
        or set(file_hashes) != set(_GOLDEN_PATHS.values())
    ):
        raise DecisionContractError("DECISION_STATIC_MANIFEST_INVALID")
    for value in file_hashes.values():
        _require_sha256(value, "DECISION_STATIC_MANIFEST_INVALID")
    return {
        "files": dict(sorted(file_hashes.items())),
        "schema_version": DECISION_STATIC_MANIFEST_SCHEMA_VERSION,
        "source_static_manifest_sha256": STATIC_MANIFEST_SHA256,
        "status": FREEZE_STATUS,
    }


def load_decision_static_manifest() -> dict[str, object]:
    raw, document = _read_canonical_json(_DECISION_STATIC_MANIFEST_PATH)
    if sha256(raw).hexdigest() != DECISION_STATIC_MANIFEST_SHA256:
        raise DecisionContractError("DECISION_STATIC_MANIFEST_HASH_MISMATCH")
    root = _require_exact_keys(
        document,
        {
            "files",
            "schema_version",
            "source_static_manifest_sha256",
            "status",
        },
        "DECISION_STATIC_MANIFEST_INVALID",
    )
    if (
        root["schema_version"] != DECISION_STATIC_MANIFEST_SCHEMA_VERSION
        or root["source_static_manifest_sha256"] != STATIC_MANIFEST_SHA256
        or root["status"] != FREEZE_STATUS
        or type(root["files"]) is not dict
        or set(root["files"]) != set(_GOLDEN_PATHS.values())
    ):
        raise DecisionContractError("DECISION_STATIC_MANIFEST_INVALID")
    for value in root["files"].values():
        _require_sha256(value, "DECISION_STATIC_MANIFEST_INVALID")
    return document


def _load_manifest_bound_golden(
    fixture_id: str,
) -> dict[str, object]:
    manifest = load_decision_static_manifest()
    relative_path = _GOLDEN_PATHS[fixture_id]
    expected_sha256 = manifest["files"].get(relative_path)
    raw, document = _read_canonical_json(_BENCHMARK_ROOT / relative_path)
    if sha256(raw).hexdigest() != expected_sha256:
        raise DecisionContractError("DECISION_STATIC_ARTIFACT_HASH_MISMATCH")
    return document


def _validate_decision_document(
    fixture_id: str,
    document: object,
) -> dict[str, object]:
    root = _require_exact_keys(
        document,
        {
            "call_semantic_output_sha256",
            "call_terminals",
            "decision_projection",
            "decision_semantic_sha256",
            "fixture_id",
            "formal_timed_receipt_permitted",
            "freeze_status",
            "lineage",
            "quantity",
            "role",
            "schema_version",
        },
        "DECISION_GOLDEN_SCHEMA_INVALID",
    )
    if (
        type(root["schema_version"]) is not str
        or root["schema_version"] != DECISION_GOLDEN_SCHEMA_VERSION
        or type(root["fixture_id"]) is not str
        or root["fixture_id"] != fixture_id
        or type(root["role"]) is not str
        or root["role"] != _ROLE_BY_FIXTURE[fixture_id]
        or type(root["formal_timed_receipt_permitted"]) is not bool
        or root["formal_timed_receipt_permitted"] != (fixture_id == "U64")
        or type(root["freeze_status"]) is not str
        or root["freeze_status"] != FREEZE_STATUS
        or type(root["quantity"]) is not int
        or root["quantity"] != QUANTITY
        or type(root["call_terminals"]) is not list
        or len(root["call_terminals"]) != FIXTURE_COUNT
        or type(root["decision_projection"]) is not dict
        or type(root["lineage"]) is not dict
    ):
        raise DecisionContractError("DECISION_GOLDEN_SCHEMA_INVALID")
    _require_sha256(
        root["call_semantic_output_sha256"],
        "DECISION_CALL_SEMANTIC_HASH_INVALID",
    )
    semantic_sha256 = _require_sha256(
        root["decision_semantic_sha256"],
        "DECISION_SEMANTIC_HASH_INVALID",
    )
    _assert_semantic_boundary(root)
    if semantic_sha256 != semantic_document_sha256(root):
        raise DecisionContractError("DECISION_SEMANTIC_HASH_MISMATCH")
    expected = derive_decision_semantic_document(fixture_id)
    if not _exact_json_equal(root, expected):
        raise DecisionContractError("DECISION_GOLDEN_CONTENT_MISMATCH")
    return root


def load_decision_semantic_golden(
    fixture_id: str,
    *,
    document: object | None = None,
) -> LoadedDecisionSemanticGoldenV1:
    if type(fixture_id) is not str or fixture_id not in _ROLE_BY_FIXTURE:
        raise DecisionContractError("DECISION_FIXTURE_ID_INVALID")
    if document is None:
        document = _load_manifest_bound_golden(fixture_id)
    validated = _validate_decision_document(fixture_id, document)
    projection_sha256 = sha256(
        canonical_json_bytes(validated["decision_projection"])
    ).hexdigest()
    frozen = _freeze_json(validated)
    if not isinstance(frozen, Mapping):
        raise DecisionContractError("DECISION_GOLDEN_SCHEMA_INVALID")
    return LoadedDecisionSemanticGoldenV1(
        fixture_id=fixture_id,
        role=validated["role"],  # type: ignore[arg-type]
        formal_timed_receipt_permitted=validated[
            "formal_timed_receipt_permitted"
        ],  # type: ignore[arg-type]
        document=frozen,
        decision_semantic_sha256=validated[
            "decision_semantic_sha256"
        ],  # type: ignore[arg-type]
        projection_sha256=projection_sha256,
    )


def assert_formal_timed_receipt_permitted(
    golden: LoadedDecisionSemanticGoldenV1,
) -> None:
    if (
        type(golden) is not LoadedDecisionSemanticGoldenV1
        or golden.fixture_id != "U64"
        or golden.role != FORMAL_ROLE
        or golden.formal_timed_receipt_permitted is not True
    ):
        raise DecisionContractError("DECISION_FORMAL_TIMED_ROLE_INVALID")
