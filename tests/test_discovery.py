"""Cohort discovery: profile parsing, domicile, and same-issuer collapsing."""

from __future__ import annotations

from basin.edgar.discovery import dedupe_issuers, profile_from_submissions


def _submissions(**overrides) -> dict:
    payload = {
        "cik": 1090012,
        "name": "TEST ENERGY CORP",
        "tickers": ["TST"],
        "exchanges": ["NYSE"],
        "sic": "1311",
        "sicDescription": "Crude Petroleum & Natural Gas",
        "addresses": {"business": {"stateOrCountry": "TX", "stateOrCountryDescription": "TX"}},
        "formerNames": [],
        "filings": {
            "recent": {
                "form": ["10-K", "8-K", "10-Q", "10-K", "8-K"],
                "filingDate": [
                    "2026-02-20",
                    "2026-02-19",
                    "2025-11-01",
                    "2025-02-20",
                    "2025-01-15",
                ],
                "accessionNumber": [
                    "0001090012-26-000010",
                    "0001090012-26-000009",
                    "0001090012-25-000030",
                    "0001090012-25-000010",
                    "0001090012-25-000001",
                ],
            }
        },
    }
    payload.update(overrides)
    return payload


class TestProfileParsing:
    def test_reads_latest_10k_not_merely_the_first_listed(self):
        p = profile_from_submissions(_submissions())
        assert p.latest_10k_date == "2026-02-20"
        assert p.latest_10k_accession == "0001090012-26-000010"
        assert p.tenk_count == 2

    def test_counts_8ks_separately(self):
        p = profile_from_submissions(_submissions())
        assert p.eightk_count == 2
        assert p.latest_8k_date == "2026-02-19"

    def test_10q_is_not_counted_as_a_10k(self):
        p = profile_from_submissions(_submissions())
        assert p.tenk_count == 2

    def test_null_exchange_entries_are_dropped(self):
        # EDGAR pads `exchanges` to match `tickers`, leaving nulls behind.
        p = profile_from_submissions(
            _submissions(tickers=["TST", "TSTB"], exchanges=["NYSE", None])
        )
        assert p.exchanges == ("NYSE",)
        assert p.tickers == ("TST", "TSTB")

    def test_filed_10k_since(self):
        p = profile_from_submissions(_submissions())
        assert p.filed_10k_since("2025-01-01")
        assert not p.filed_10k_since("2027-01-01")


class TestDomicile:
    def test_us_filer_is_not_foreign(self):
        # EDGAR echoes the state code as its own description for US filers.
        p = profile_from_submissions(_submissions())
        assert not p.is_foreign

    def test_foreign_filer_is_detected_by_a_real_description(self):
        p = profile_from_submissions(
            _submissions(
                addresses={
                    "business": {
                        "stateOrCountry": "A0",
                        "stateOrCountryDescription": "Alberta, Canada",
                    }
                }
            )
        )
        assert p.is_foreign

    def test_missing_address_is_not_treated_as_foreign(self):
        p = profile_from_submissions(_submissions(addresses={}))
        assert not p.is_foreign


class TestIssuerDedup:
    def test_shared_ticker_collapses_to_one_issuer(self):
        old = profile_from_submissions(
            _submissions(
                cik=6769,
                name="APACHE CORP",
                filings={
                    "recent": {
                        "form": ["10-K"],
                        "filingDate": ["2021-02-25"],
                        "accessionNumber": ["0000006769-21-000010"],
                    }
                },
            )
        )
        new = profile_from_submissions(_submissions(cik=1841666, name="APA CORPORATION"))

        groups = dedupe_issuers([old, new])
        assert len(groups) == 1
        # The CIK still filing is the one the cohort should track.
        assert groups[0].primary.cik == "0001841666"
        assert groups[0].superseded[0].cik == "0000006769"

    def test_former_name_collapses_a_reorganisation(self):
        successor = profile_from_submissions(
            _submissions(
                cik=2074176,
                name="Viper Energy, Inc.",
                tickers=["VNOM"],
            )
        )
        predecessor = profile_from_submissions(
            _submissions(
                cik=1602065,
                name="VNOM Sub, Inc.",
                tickers=[],
                formerNames=[{"name": "Viper Energy, Inc."}],
                filings={
                    "recent": {
                        "form": ["10-K"],
                        "filingDate": ["2025-02-25"],
                        "accessionNumber": ["0001602065-25-000010"],
                    }
                },
            )
        )
        groups = dedupe_issuers([successor, predecessor])
        assert len(groups) == 1
        assert groups[0].primary.cik == "0002074176"
        assert "former name" in groups[0].reason

    def test_distinct_companies_are_not_merged(self):
        a = profile_from_submissions(_submissions(cik=1090012, name="DEVON ENERGY CORP", tickers=["DVN"]))
        b = profile_from_submissions(_submissions(cik=1539838, name="Diamondback Energy, Inc.", tickers=["FANG"]))
        assert len(dedupe_issuers([a, b])) == 2

    def test_untickered_filers_do_not_all_collapse_together(self):
        # Absent evidence, CIKs stay separate: wrongly merging two real
        # companies silently drops one from the cohort.
        a = profile_from_submissions(_submissions(cik=111, name="ALPHA OIL", tickers=[]))
        b = profile_from_submissions(_submissions(cik=222, name="BETA GAS", tickers=[]))
        assert len(dedupe_issuers([a, b])) == 2
