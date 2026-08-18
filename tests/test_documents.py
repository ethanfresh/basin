"""Document verification: does the value actually appear in the filing?

These are the checks that turn an accession from an assertion into a citation.
All offline — the HTML shapes here are copied from real filings.
"""

from __future__ import annotations

import pytest

from basin.documents import find_value, html_to_text
from basin.documents.locate import document_url, primary_document


class TestHtmlToText:
    def test_table_cells_do_not_fuse(self):
        # Without a boundary, "12" and "34" become "1234" — a number the
        # filing never contained.
        text = html_to_text("<table><tr><td>12</td><td>34</td></tr></table>")
        assert "1234" not in text
        assert "12" in text and "34" in text

    def test_drops_script_and_style(self):
        text = html_to_text("<style>.a{color:red}</style><p>Reserves 1,234</p>")
        assert "color" not in text
        assert "1,234" in text

    def test_unescapes_entities_and_nbsp(self):
        text = html_to_text("<p>2,521&nbsp;028 &amp; more</p>")
        assert "&" in text and "&amp;" not in text

    def test_normalises_unicode_dashes(self):
        assert "-500" in html_to_text("<p>−500</p>")


class TestFindValue:
    def test_finds_a_value_printed_as_tagged(self):
        match = find_value("Proved reserves 13,783 Bcfe", 13_783.0)
        assert match is not None
        assert match.printed == "13,783"
        assert match.scale == 1.0

    def test_finds_a_value_the_filing_printed_in_thousands(self):
        # Diamondback's shape: tagged 2,521,028,000, printed 2,521,028.
        match = find_value("December 31, 2025 2,521,028", 2_521_028_000.0)
        assert match is not None
        assert match.printed == "2,521,028"
        assert match.scale == 1e3
        assert match.scale_label == "thousands"

    def test_returns_none_when_absent(self):
        assert find_value("nothing numeric of interest here", 987_654.0) is None

    def test_does_not_match_a_fragment_of_a_longer_number(self):
        # 1,234 must not match inside 11,234 or 1,234,567.
        assert find_value("total 1,234,567", 1_234.0) is None
        assert find_value("total 11,234", 1_234.0) is None

    def test_counts_occurrences_for_confidence(self):
        text = "A 13,783 B 13,783 C 13,783 D 13,783"
        match = find_value(text, 13_783.0)
        assert match.hits == 4
        assert not match.unambiguous

    def test_single_occurrence_is_unambiguous(self):
        assert find_value("only once 13,783 here", 13_783.0).unambiguous

    def test_refuses_values_too_short_to_be_evidence(self):
        # A two-digit number matches half a filing; finding it proves nothing.
        assert find_value("the year 2024 and 12 wells", 12.0) is None

    def test_captures_a_quotable_source_span(self):
        match = find_value("Proved developed reserves December 31, 2025 13,783 Bcfe", 13_783.0)
        assert "Proved developed reserves" in match.source_span
        assert "13,783" in match.source_span


class TestLocate:
    def test_document_url_uses_unpadded_cik_and_stripped_accession(self):
        url = document_url("0001539838", "0001539838-26-000010", "fang-20251231.htm")
        assert url == (
            "https://www.sec.gov/Archives/edgar/data/1539838/"
            "000153983826000010/fang-20251231.htm"
        )

    def test_primary_document_without_cache_or_client_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("basin.documents.locate.SUBMISSIONS_CACHE", tmp_path)
        assert primary_document("0001539838", "0001539838-26-000010") is None


class TestUnitConversion:
    def test_barrels_and_boe_are_the_same_size(self):
        from basin.facts.units import conversion_for

        assert conversion_for("bbl").factor == 1.0
        assert conversion_for("MBoe").factor == 1e3
        assert conversion_for("MMBoe").factor == 1e6

    def test_gas_conversion_is_flagged_as_a_convention(self):
        from basin.facts.units import conversion_for

        gas = conversion_for("Bcfe")
        assert gas.is_convention and "6" in gas.note
        # 1 Bcf = 1e9 cubic feet, at 6,000 cubic feet per BOE.
        assert gas.factor == pytest.approx(1e9 / 6_000)

    def test_rate_units_have_no_canonical_form(self):
        from basin.facts.units import conversion_for

        assert conversion_for("USD/bbl") is None

    def test_normalise_applies_scale_then_unit(self):
        from basin.facts.units import normalise

        value, unit, _ = normalise(2_521_028_000.0, "MBoe", 1e3)
        assert unit == "BOE"
        assert value == pytest.approx(2_521_028_000.0)


class TestScaleResolution:
    """Verified scale gives two candidate readings; economics picks one."""

    def test_picks_the_printed_reading_when_as_tagged_is_absurd(self):
        from basin.facts.scale import resolve

        # Diamondback: as tagged implies $0.01/BOE, descaled implies $10.20.
        r = resolve(3_617_856_000.0, "MBoe", 1e3, 36_910_000_000.0, 1e3)
        assert r.status == "resolved"
        assert r.reserve_divisor == 1e3
        assert r.usd_per_boe == pytest.approx(10.2, abs=0.1)

    def test_keeps_the_tagged_reading_when_it_is_the_sensible_one(self):
        from basin.facts.scale import resolve

        # CNX verifies at the same scale as Diamondback and needs the
        # opposite answer, which is why the scale alone cannot decide.
        r = resolve(9_662_144_000.0, "Mcfe", 1e3, 5_066_306_000.0, 1e3)
        assert r.status == "resolved"
        assert r.reserve_divisor == 1.0
        assert r.usd_per_boe == pytest.approx(3.15, abs=0.1)

    def test_without_a_standardized_measure_nothing_is_decided(self):
        from basin.facts.scale import resolve

        r = resolve(1_000.0, "MBoe", 1e3, None, None)
        assert r.status == "unavailable"
        assert r.reserve_divisor is None

    def test_records_what_it_rejected(self):
        from basin.facts.scale import resolve

        r = resolve(3_617_856_000.0, "MBoe", 1e3, 36_910_000_000.0, 1e3)
        assert "$/BOE" in r.rejected or "/BOE" in r.rejected

    def test_absurd_on_every_reading_stays_ambiguous(self):
        from basin.facts.scale import resolve

        r = resolve(1.0, "MBoe", 1e3, 1e15, 1.0)
        assert r.status == "ambiguous"
        assert r.reserve_divisor is None
