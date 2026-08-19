"""Per-period alias validation: the drift that a single verdict hid."""

from __future__ import annotations

from basin.facts.validation import (
    STATUS_DRIFTED,
    STATUS_INCOHERENT,
    STATUS_VALIDATED,
    validate_reserve_family,
)


def _payload(dev: dict, undev: dict, total: dict, cik: int = 732834) -> dict:
    def series(tag, values):
        return {
            tag: {
                "units": {
                    "MBoe": [
                        {"end": end, "val": val, "form": "10-K",
                         "accn": f"0000-{end[:4]}-1", "filed": f"{int(end[:4]) + 1}-02-20"}
                        for end, val in values.items()
                    ]
                }
            }
        }

    srt = {}
    srt.update(series("ProvedDevelopedReservesBOE1", dev))
    srt.update(series("ProvedUndevelopedReserveBOE1", undev))
    srt.update(series("ProvedDevelopedAndUndevelopedReserveNetEnergy", total))
    return {"cik": cik, "facts": {"srt": srt}}


class TestPerPeriodCoherence:
    def test_a_clean_filer_validates_every_period(self):
        v = validate_reserve_family(
            _payload({"2024-12-31": 60.0, "2025-12-31": 70.0},
                     {"2024-12-31": 40.0, "2025-12-31": 30.0},
                     {"2024-12-31": 100.0, "2025-12-31": 100.0})
        )
        assert v.status == STATUS_VALIDATED
        assert v.coherent_period_ends == {"2024-12-31", "2025-12-31"}
        assert v.incoherent_period_ends == frozenset()
        assert v.holds_for("2025-12-31")

    def test_a_tag_that_changes_meaning_marks_only_the_bad_periods(self):
        # Continental's shape: the tags carry what they say through 2020 and
        # swap undeveloped with total from 2021 on. Suppressing the filer's
        # whole history would trade one wrong answer for eight missing ones.
        v = validate_reserve_family(
            _payload(
                {"2019-12-31": 60.0, "2020-12-31": 60.0,
                 "2021-12-31": 60.0, "2022-12-31": 60.0},
                {"2019-12-31": 40.0, "2020-12-31": 40.0,
                 "2021-12-31": 100.0, "2022-12-31": 100.0},   # actually the total
                {"2019-12-31": 100.0, "2020-12-31": 100.0,
                 "2021-12-31": 40.0, "2022-12-31": 40.0},     # actually undeveloped
            )
        )
        assert v.status == STATUS_DRIFTED
        assert v.coherent_period_ends == {"2019-12-31", "2020-12-31"}
        assert v.incoherent_period_ends == {"2021-12-31", "2022-12-31"}
        assert v.holds_for("2020-12-31")
        assert not v.holds_for("2021-12-31")

    def test_the_suppressed_periods_are_named_in_the_note(self):
        # "Continental has no FY2022 proved total" is a question someone asks,
        # and the answer has to be somewhere a person can read it.
        v = validate_reserve_family(
            _payload({"2021-12-31": 60.0, "2022-12-31": 60.0},
                     {"2021-12-31": 40.0, "2022-12-31": 100.0},
                     {"2021-12-31": 100.0, "2022-12-31": 40.0})
        )
        assert "2022-12-31" in v.note
        assert "suppressed" in v.note

    def test_an_untested_period_is_not_treated_as_a_failure(self):
        # Two of three concepts untagged is a coverage gap, not evidence of a
        # wrong number. Filtering on coherent_period_ends alone would drop it.
        v = validate_reserve_family(
            _payload({"2024-12-31": 60.0, "2025-12-31": 70.0},
                     {"2024-12-31": 40.0, "2025-12-31": 30.0},
                     {"2024-12-31": 100.0})
        )
        assert "2025-12-31" not in v.coherent_period_ends
        assert v.holds_for("2025-12-31")

    def test_a_filer_that_never_closes_is_incoherent_and_wholly_suppressed(self):
        v = validate_reserve_family(
            _payload({"2024-12-31": 60.0}, {"2024-12-31": 40.0}, {"2024-12-31": 999.0})
        )
        assert v.status == STATUS_INCOHERENT
        assert v.coherent_period_ends == frozenset()
        assert v.incoherent_period_ends == {"2024-12-31"}

    def test_a_zero_total_is_untested_not_failed(self):
        # Nothing to divide by. Counting it as a failure would suppress a
        # period on the strength of an arithmetic accident.
        v = validate_reserve_family(
            _payload({"2024-12-31": 0.0}, {"2024-12-31": 0.0}, {"2024-12-31": 0.0})
        )
        assert "2024-12-31" not in v.incoherent_period_ends


class TestSuppression:
    def _validation_and_rows(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("ix", "scripts/ingest_xbrl.py")
        ix = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ix)
        return ix

    def test_only_the_rejected_tag_and_period_is_dropped(self):
        from dataclasses import dataclass

        ix = self._validation_and_rows()
        v = validate_reserve_family(
            _payload({"2020-12-31": 60.0, "2021-12-31": 60.0},
                     {"2020-12-31": 40.0, "2021-12-31": 100.0},
                     {"2020-12-31": 100.0, "2021-12-31": 40.0})
        )

        @dataclass
        class Row:
            concept_key: str
            taxonomy: str
            tag: str
            period_end: str

        rows = [
            Row("proved_reserves_boe", "srt",
                "ProvedDevelopedAndUndevelopedReserveNetEnergy", "2021-12-31"),
            Row("proved_reserves_boe", "srt",
                "ProvedDevelopedAndUndevelopedReserveNetEnergy", "2020-12-31"),
            # A different alias for the same concept was never tested.
            Row("proved_reserves_boe", "us-gaap", "SomeOtherTag", "2021-12-31"),
            # An unrelated concept in a rejected period is not implicated.
            Row("production_volume", "srt", "ProductionVolume", "2021-12-31"),
        ]
        kept, dropped = ix._drop_incoherent(rows, v)
        assert dropped == 1
        assert [r.period_end for r in kept if r.concept_key == "proved_reserves_boe"] == [
            "2020-12-31", "2021-12-31",
        ]

    def test_nothing_is_dropped_when_the_family_validates(self):
        ix = self._validation_and_rows()
        v = validate_reserve_family(
            _payload({"2025-12-31": 60.0}, {"2025-12-31": 40.0}, {"2025-12-31": 100.0})
        )
        rows = [object()]
        kept, dropped = ix._drop_incoherent(rows, v)
        assert dropped == 0 and kept == rows
