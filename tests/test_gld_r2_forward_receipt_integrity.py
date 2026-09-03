from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import replace
from datetime import date
from hashlib import sha256
import pickle
import unittest

from gld_management_research.xnys_calendar import (
    action_1045_utc_ns,
    next_session_date,
)
from gld_r2_theta_qualification.contracts import (
    R2QualificationError,
    canonical_json_bytes,
    canonical_sha256,
)
from gld_r2_theta_qualification.forward import (
    SyntheticForwardDiagnosticReceipt,
    build_synthetic_forward_diagnostic_receipt,
    record_synthetic_forward_session,
    record_synthetic_forward_terminal_episode,
    start_synthetic_exact_100_forward,
)


def _hash(label: str) -> str:
    return sha256(label.encode("ascii")).hexdigest()


def _session_dates(count: int) -> tuple[date, ...]:
    values = [date(2027, 1, 4)]
    while len(values) < count:
        values.append(next_session_date(values[-1]))
    return tuple(values)


class SyntheticForwardReceiptIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        first_session = date(2027, 1, 4)
        state = start_synthetic_exact_100_forward(
            synthetic_theta_sha256=_hash("historical-theta"),
            t0_utc_ns=action_1045_utc_ns(first_session) - 1,
        )
        dates = _session_dates(100)
        for ordinal, session_date in enumerate(dates, start=1):
            session_id = f"XNYS-{session_date.isoformat()}"
            state = record_synthetic_forward_session(
                state,
                session_id=session_id,
                session_date=session_date.isoformat(),
                session_ordinal=ordinal,
                observation_utc_ns=action_1045_utc_ns(session_date),
                input_sha256=_hash(f"session-{ordinal}"),
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
                synthetic_return_ppm=20_000,
                input_sha256=_hash(f"episode-{ordinal}"),
            )
        cls.sealed_state = state

    def _receipt(self) -> SyntheticForwardDiagnosticReceipt:
        return build_synthetic_forward_diagnostic_receipt(
            self.sealed_state,
            synthetic_scenario_sha256=_hash("research-freeze"),
            test_replicates=8,
            test_lower_bound_index=0,
        )

    def test_copy_and_deepcopy_reuse_the_exact_immutable_instance(self) -> None:
        receipt = self._receipt()

        self.assertIs(copy(receipt), receipt)
        self.assertIs(deepcopy(receipt), receipt)

    def test_pickle_cannot_reconstruct_a_new_seal_owner_cycle(self) -> None:
        with self.assertRaisesRegex(
            TypeError,
            "synthetic Forward diagnostic receipts cannot be pickled",
        ):
            pickle.dumps(self._receipt())

    def test_plain_dataclass_replace_cannot_reuse_instance_seal(self) -> None:
        replaced = replace(self._receipt())

        with self.assertRaises(R2QualificationError) as caught:
            replaced.as_dict()
        self.assertEqual(
            caught.exception.reason_code,
            "SYNTHETIC_DIAGNOSTIC_RECEIPT_INTEGRITY_MISMATCH",
        )

    def test_replace_cannot_forge_self_consistent_lcb_conclusion(self) -> None:
        receipt = self._receipt()
        forged_unsigned = receipt._unsigned_dict()
        forged_unsigned["synthetic_lower_bound_ppm"] = -1
        forged_unsigned["reason_code"] = "SYNTHETIC_FORWARD_LCB_NONPOSITIVE"
        forged_hash = canonical_sha256(forged_unsigned)
        forged_document = {
            **forged_unsigned,
            "diagnostic_receipt_sha256": forged_hash,
        }
        forged = replace(
            receipt,
            synthetic_lower_bound_ppm=-1,
            reason_code="SYNTHETIC_FORWARD_LCB_NONPOSITIVE",
            diagnostic_receipt_sha256=forged_hash,
            _canonical_bytes=canonical_json_bytes(forged_document),
        )

        with self.assertRaises(R2QualificationError) as caught:
            forged.as_dict()
        self.assertEqual(
            caught.exception.reason_code,
            "SYNTHETIC_DIAGNOSTIC_RECEIPT_INTEGRITY_MISMATCH",
        )

    def test_transplanting_another_receipt_seal_is_rejected(self) -> None:
        first = self._receipt()
        second = self._receipt()
        transplanted = replace(first, _seal=second._seal)

        with self.assertRaises(R2QualificationError) as caught:
            transplanted.as_dict()
        self.assertEqual(
            caught.exception.reason_code,
            "SYNTHETIC_DIAGNOSTIC_RECEIPT_INTEGRITY_MISMATCH",
        )

    def test_as_dict_revalidates_after_a_successful_read(self) -> None:
        receipt = self._receipt()
        self.assertEqual(receipt.as_dict()["classification"], "SYNTHETIC_ONLY")
        object.__setattr__(
            receipt,
            "_canonical_bytes",
            b" " + receipt._canonical_bytes,
        )

        with self.assertRaises(R2QualificationError) as caught:
            receipt.as_dict()
        self.assertEqual(
            caught.exception.reason_code,
            "SYNTHETIC_DIAGNOSTIC_RECEIPT_INTEGRITY_MISMATCH",
        )


if __name__ == "__main__":
    unittest.main()
