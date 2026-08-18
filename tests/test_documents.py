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


class TestHeaderUnits:
    """The filing's own table header outranks the tagged unit."""

    def test_reads_a_unit_stated_inline(self):
        from basin.documents.headers import unit_hints

        text = "our estimated proved reserves were 3,617,856 MBOE at year end"
        hints = unit_hints(text, text.index("3,617,856"), len("3,617,856"))
        assert hints[0].unit == "MBOE"
        assert hints[0].confident

    def test_reads_units_from_a_column_header(self):
        from basin.documents.headers import unit_hints

        text = "Oil (MMBbl) Natural Gas (Bcf) NGL (MMBbl) Total (Bcfe) Total proved 24 3,612 83 4,253"
        hints = unit_hints(text, text.index("4,253"), len("4,253"))
        assert {h.unit for h in hints} >= {"Bcfe", "Bcf", "MMBbl"}
        assert all(h.kind == "header" for h in hints)

    def test_nearest_header_comes_first(self):
        from basin.documents.headers import unit_hints

        text = "(MMBbl) then later (Bcfe) and the figure 4,253"
        assert unit_hints(text, text.index("4,253"), 5)[0].unit == "Bcfe"

    def test_corrects_a_mislabelled_unit_family(self):
        from basin.facts.scale import resolve

        # Gulfport tags total proved in bbl; its table says Total (Bcfe).
        # Read as barrels it implies $0.80/BOE, which clears the wide band
        # and is not a number a producer reports.
        r = resolve(
            4_253_000_000.0, "bbl", 1e6, 3_401_000_000.0, 1.0,
            header_units=("MMBbl", "Bcf", "Bcfe"),
        )
        assert r.status == "resolved"
        assert r.unit_corrected
        assert r.reserve_unit in {"Bcf", "Bcfe"}
        assert 1.5 <= r.usd_per_boe <= 50

    def test_leaves_a_correct_tagged_unit_alone(self):
        from basin.facts.scale import resolve

        r = resolve(
            3_617_856_000.0, "MBoe", 1e3, 36_910_000_000.0, 1e3,
            header_units=("MBOE",),
        )
        assert not r.unit_corrected
        assert r.reserve_unit == "MBoe"

    def test_header_units_that_help_nothing_are_ignored(self):
        from basin.facts.scale import resolve

        r = resolve(
            3_617_856_000.0, "MBoe", 1e3, 36_910_000_000.0, 1e3,
            header_units=("Bcf", "MMBbl", "Tcf"),
        )
        assert r.reserve_unit == "MBoe"
        assert not r.unit_corrected


class TestExhibitDetection:
    """What an attachment *is* comes from EDGAR's declared type, not its name."""

    INDEX_HTML = """
    <table>
      <tr><td>1</td><td>FORM 8-K</td><td>bdco20260522_8k.htm iXBRL</td><td>8-K</td><td>25904</td></tr>
      <tr><td>2</td><td>EXHIBIT 99.1 EARNINGS RLS</td><td>ex_967513.htm</td><td>EX-99.1</td><td>162964</td></tr>
      <tr><td>3</td><td>MATERIAL AGREEMENT</td><td>ef20080297_ex10-1.htm</td><td>EX-10.1</td><td>4000</td></tr>
      <tr><td>4</td><td></td><td>logo.jpg</td><td>GRAPHIC</td><td>5607</td></tr>
    </table>
    """

    class _Client:
        def __init__(self, html):
            self.html = html

        def get_text(self, url):
            return self.html

    def test_finds_an_exhibit_whose_filename_says_nothing(self):
        from basin.documents.locate import earnings_exhibits

        client = self._Client(self.INDEX_HTML)
        # "ex_967513.htm" contains no "99" at all; only the type column does.
        assert earnings_exhibits(client, "1", "0001437749-26-028116") == [
            "ex_967513.htm"
        ]

    def test_ignores_a_material_agreement_exhibit(self):
        from basin.documents.locate import earnings_exhibits

        # EX-10.1 is a contract, not an earnings release, and carrying it into
        # a guidance corpus would be noise.
        found = earnings_exhibits(self._Client(self.INDEX_HTML), "1", "acc")
        assert not any("ex10" in name for name in found)

    def test_ignores_non_html_attachments(self):
        from basin.documents.locate import earnings_exhibits

        assert "logo.jpg" not in earnings_exhibits(
            self._Client(self.INDEX_HTML), "1", "acc"
        )

    def test_reads_every_declared_type(self):
        from basin.documents.locate import index_documents

        docs = index_documents(self._Client(self.INDEX_HTML), "1", "acc")
        assert [d["type"] for d in docs] == ["8-K", "EX-99.1", "EX-10.1", "GRAPHIC"]
        # The document cell can carry a trailing "iXBRL" marker.
        assert docs[0]["document"] == "bdco20260522_8k.htm"
