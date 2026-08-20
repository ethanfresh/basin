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

        # The evidence for a choice includes the readings not taken. The
        # wording is deliberately not asserted: a reading can now be rejected
        # on magnitude before the value-per-barrel test runs at all.
        r = resolve(3_617_856_000.0, "MBoe", 1e3, 36_910_000_000.0, 1e3)
        assert r.rejected
        assert "as tagged" in r.rejected

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


class TestTableStructure:
    """D2: a cell without its column header is a number without a meaning."""

    GULFPORT = """
    <table>
      <tr><td></td><td></td></tr>
      <tr><td>December 31, 2025</td></tr>
      <tr><td>Oil (MMBbl)</td><td>Natural Gas (Bcf)</td><td>NGL (MMBbl)</td><td>Total (Bcfe)</td></tr>
      <tr><td>Total proved</td><td>19</td><td>2,906</td><td>52</td><td>3,328</td></tr>
    </table>
    """

    def test_attaches_the_column_header_to_a_cell(self):
        from basin.documents.tables import header_for_value, parse_tables

        # This is the header that would have prevented Gulfport's reserves
        # being stored as barrels: the column says Bcfe.
        assert header_for_value(parse_tables(self.GULFPORT), "3,328") == (
            "Total (Bcfe)", "Total proved",
        )

    def test_headers_align_by_numeric_position_not_raw_index(self):
        from basin.documents.tables import header_for_value, parse_tables

        # The data row carries a leading label the header row does not, so raw
        # column indices are offset by one between them.
        assert header_for_value(parse_tables(self.GULFPORT), "2,906")[0] == (
            "Natural Gas (Bcf)"
        )

    def test_a_leading_blank_row_does_not_hide_the_headers(self):
        from basin.documents.tables import parse_tables

        # A blank spacing row used to flip the parser into "data" mode, after
        # which every header row was read as data and no cell had a header.
        table = parse_tables(self.GULFPORT)[0]
        assert table.column_labels[:2] == ["Oil (MMBbl)", "Natural Gas (Bcf)"]

    def test_a_value_outside_any_table_returns_nothing(self):
        from basin.documents.tables import header_for_value, parse_tables

        assert header_for_value(parse_tables(self.GULFPORT), "99,999") is None


class TestMarkupLookup:
    """D1/D3: identify the fact, do not match a string that resembles it."""

    DOC = """
    <html><body>
      <xbrli:context id="c-1"><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
      <xbrli:context id="c-2"><xbrli:entity><xbrli:segment>
        <xbrldi:explicitMember dimension="srt:ReserveQuantitiesByTypeOfReserveAxis">srt:CrudeOilMember</xbrldi:explicitMember>
      </xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
      <xbrli:unit id="u1"><xbrli:measure>utr:MBoe</xbrli:measure></xbrli:unit>
      <p>An unrelated 3,617,856 appearing first in the document.</p>
      <p><ix:nonFraction name="srt:ProvedDevelopedAndUndevelopedReserveNetEnergy"
         contextRef="c-1" unitRef="u1" scale="3" id="f-9">3,617,856</ix:nonFraction></p>
      <p><ix:nonFraction name="srt:ProvedDevelopedAndUndevelopedReserveNetEnergy"
         contextRef="c-2" unitRef="u1" scale="3" id="f-10">1,774,420</ix:nonFraction></p>
    </body></html>
    """

    def test_reads_the_declared_scale_rather_than_inferring_it(self):
        from basin.documents.inline import tagged_figures

        figure = next(f for f in tagged_figures(self.DOC) if f.element_id == "f-9")
        assert figure.scale == 3
        assert figure.value == 3_617_856_000.0
        assert figure.shown == "3,617,856"

    def test_matches_the_tagged_fact_not_the_first_lookalike(self):
        from basin.documents.inline import match_fact, tagged_figures

        figure = match_fact(
            tagged_figures(self.DOC),
            concept_tag="ProvedDevelopedAndUndevelopedReserveNetEnergy",
            period_end="2025-12-31",
            value=3_617_856_000.0,
        )
        # The plain-text 3,617,856 occurs earlier; a string search would take it.
        assert figure.element_id == "f-9"
        assert figure.anchor == "#f-9"

    def test_distinguishes_two_facts_by_product_dimension(self):
        from basin.documents.inline import match_fact, tagged_figures

        figures = tagged_figures(self.DOC)
        oil = match_fact(
            figures,
            concept_tag="ProvedDevelopedAndUndevelopedReserveNetEnergy",
            period_end="2025-12-31",
            value=1_774_420_000.0,
            product="oil",
        )
        assert oil.product == "oil" and oil.element_id == "f-10"

    def test_refuses_a_value_match_against_the_wrong_concept(self):
        from basin.documents.inline import match_fact, tagged_figures

        assert match_fact(
            tagged_figures(self.DOC),
            concept_tag="SomeOtherConcept",
            period_end="2025-12-31",
            value=3_617_856_000.0,
        ) is None

    def test_refuses_a_value_match_with_no_concept_to_match_on(self):
        """A figure that agrees only on value and period is not evidence.

        The guard above reads `if concept_tag`, so a caller that withheld the
        tag turned it off and took the earliest lookalike in the document.
        """
        from basin.documents.inline import match_fact, tagged_figures

        assert match_fact(
            tagged_figures(self.DOC),
            concept_tag=None,
            period_end="2025-12-31",
            value=3_617_856_000.0,
        ) is None


class TestSectionAndFolio:
    def test_heading_split_across_table_cells_is_recovered(self):
        from basin.documents.text import parse, section_of

        # D5: filings put the number and title in separate cells.
        doc = parse("<table><tr><td>Item</td><td>1.</td><td>Business</td></tr></table><p>x 1,234</p>")
        assert section_of(doc.text, doc.text.index("1,234")) == "Item 1. Business"

    def test_printed_folio_is_captured_separately_from_the_page_count(self):
        from basin.documents.text import parse

        # D6: the number printed on a page need not equal the page's ordinal.
        doc = parse("<p>text</p><p>6</p><hr><p>more</p><p>7</p>")
        assert doc.pages == 2
        assert doc.folio(1) == 6 and doc.folio(2) == 7

    # A 10-K's contents page lists every item before the body begins, laid out
    # as EQT lays it out: number, title, and the page it points to.
    CONTENTS = """
    <p>TABLE OF CONTENTS</p><p>Page</p>
    <p>Item 1.</p><p>Business</p><p>8</p>
    <p>Item 1A.</p><p>Risk Factors</p><p>34</p>
    <p>Item 2.</p><p>Properties</p><p>60</p>
    <p>Item 15.</p><p>Exhibits and Financial Statement Schedules</p><p>149</p>
    <p>Item 16.</p><p>Form 10-K Summary</p><p>155</p>
    <hr>
    <p>Item 1.&#160;&#160;&#160;&#160;&#160;&#160;&#160;&#160;Business</p>
    <p>We produced 1,234 Bcfe.</p>
    <hr>
    <p>Item 16.&#160;&#160;&#160;&#160;Form 10-K Summary</p><p>None.</p>
    """

    def test_the_table_of_contents_is_not_mistaken_for_the_body(self):
        from basin.documents.text import parse, section_of

        # D9: taking the last heading before an offset put a figure on page 2
        # under Item 16, because the contents list every item before the body
        # starts. The figure sits in Item 1.
        doc = parse(self.CONTENTS)
        assert section_of(doc.text, doc.text.index("1,234")) == "Item 1. Business"

    def test_the_real_late_heading_still_governs_what_follows_it(self):
        from basin.documents.text import parse, section_of

        # Dropping the contents must not drop the sections they list.
        doc = parse(self.CONTENTS)
        assert section_of(doc.text, doc.text.index("None.")) == "Item 16. Form 10-K Summary"

    def test_a_real_heading_below_the_contents_survives_them(self):
        from basin.documents.text import parse, section_of

        # Comstock's body Item 1 sits immediately under the contents block and
        # above the page's printed folio, so it reads as one more contents row.
        # The list restarting at Item 1 is what marks the block as over.
        doc = parse("""
        <p>Item 2.</p><p>MD&amp;A</p><p>22</p>
        <p>Item 3.</p><p>Market Risk</p><p>26</p>
        <p>Item 4.</p><p>Controls and Procedures</p><p>27</p>
        <p>Item 6.</p><p>Exhibits</p><p>28</p>
        <hr>
        <p>ITEM 1. FINANCIAL STATEMENTS</p><p>3</p>
        <p>Net cash provided by operating activities 389,955</p>
        """)
        assert section_of(doc.text, doc.text.index("389,955")) == "Item 1. FINANCIAL STATEMENTS"

    def test_contents_that_restart_at_each_part_are_still_contents(self):
        from basin.documents.text import parse, section_of

        # A 10-Q lists Part I as 1-4 and then Part II from 1 again. Reading the
        # restart as the end of the contents left Chesapeake's balance sheet
        # under "Item 4. Controls and Procedures".
        doc = parse("""
        <p>Item 1.</p><p>Financial Statements</p>
        <p>Item 2.</p><p>MD&amp;A</p><p>63</p>
        <p>Item 3.</p><p>Market Risk</p><p>95</p>
        <p>Item 4.</p><p>Controls and Procedures</p><p>102</p>
        <p>Item 1.</p><p>Legal Proceedings</p><p>103</p>
        <p>Item 1A.</p><p>Risk Factors</p><p>103</p>
        <p>Item 2.</p><p>Unregistered Sales</p><p>103</p>
        <p>Item 6.</p><p>Exhibits</p><p>104</p>
        <hr>
        <p>Total assets 1,234</p>
        """)
        assert section_of(doc.text, doc.text.index("1,234")) == "Item 1. Financial Statements"

    def test_a_lone_heading_above_a_folio_is_not_read_as_contents(self):
        from basin.documents.text import parse, section_of

        # "Item 6. [Reserved]" carries nothing but the page number printed
        # under it, which looks exactly like a contents row on its own. Only a
        # run of such rows is a table of contents.
        doc = parse("<p>Item 6.</p><p>Reserved</p><p>64</p><hr><p>x 1,234</p>")
        assert section_of(doc.text, doc.text.index("1,234")) == "Item 6. Reserved"


class TestHeaderCoverageFixes:
    """The two failure modes behind 43% missing headers, verified fixed."""

    YEARS = """
    <table>
      <tr><td>December 31,</td></tr>
      <tr><td>2024</td><td>2023</td></tr>
      <tr><td>Cash</td><td>2,960,151</td><td>4,059,182</td></tr>
    </table>
    """

    def test_a_row_of_bare_years_is_a_header(self):
        from basin.documents.tables import header_for_value, parse_tables

        # "2024 2023" is entirely numeric and was classified as data, taking
        # the real header with it. It names the columns.
        found = header_for_value(parse_tables(self.YEARS), "2,960,151")
        assert found == ("2024", "Cash")

    def test_position_disambiguates_repeated_values(self):
        from basin.documents.tables import header_for_value, parse_tables

        raw = """
        <table><tr><td>Oil (MMBbl)</td></tr><tr><td>Total</td><td>4,253</td></tr></table>
        <p>filler</p>
        <table><tr><td>Total (Bcfe)</td></tr><tr><td>Total proved</td><td>4,253</td></tr></table>
        """
        tables = parse_tables(raw)
        near_second = raw.rindex("4,253")
        header, _ = header_for_value(tables, "4,253", near=near_second)
        # Without `near`, the first table's header would win.
        assert header == "Total (Bcfe)"

    def test_period_labels_survive_alongside_word_headers(self):
        from basin.documents.tables import parse_tables

        raw = """
        <table>
          <tr><td>Oil (MMBbl)</td><td>Gas (Bcf)</td></tr>
          <tr><td>2024</td><td>2023</td></tr>
          <tr><td>Total</td><td>19</td><td>3,612</td></tr>
        </table>
        """
        table = parse_tables(raw)[0]
        assert len(table.header_rows) == 2


class TestOffsetIntegrity:
    def test_cleaned_text_offsets_equal_raw_offsets(self):
        from basin.documents.text import _DROP

        raw = "<head><style>.a{}</style></head><body>Reserves 1,234</body>"
        body = _DROP.sub(lambda m: " " * len(m.group(0)), raw)
        # Every offset into the cleaned body must be valid in the raw HTML,
        # or markup positions and text positions drift apart.
        assert len(body) == len(raw)
        assert body.index("1,234") == raw.index("1,234")

    def test_hidden_ix_header_is_not_page_content(self):
        from basin.documents.text import parse

        raw = (
            "<ix:header><xbrli:context id='c-1'>Total (Bcfe) hidden</xbrli:context></ix:header>"
            "<p>Visible text 1,234</p>"
        )
        doc = parse(raw)
        # The preamble's text must not be findable as page content.
        assert "hidden" not in doc.text
        assert "1,234" in doc.text


class TestUnitOverridePreference:
    """Which reading wins when the tagged unit and a table header disagree.

    A table header is evidence about the unit, not proof of it: it is read from
    whichever table the value was located in, and a filing has many tables. So
    it breaks ties -- it does not overrule a reading that implies a far more
    plausible value per barrel.
    """

    def test_header_wins_when_the_tagged_unit_is_implausible(self):
        # Gulfport: tagged bbl implies $0.80/BOE, its own table says Bcfe and
        # implies $4.80. Only one reading is inside the typical band.
        from basin.facts.scale import resolve

        r = resolve(4_253_000_000.0, "bbl", 1e6, 3_403_000_000.0, None,
                    header_units=("Bcfe",))
        assert r.status == "resolved"
        assert r.reserve_unit == "Bcfe"
        assert r.unit_corrected
        assert 4.0 < r.usd_per_boe < 6.0

    def test_tagged_unit_wins_when_it_is_the_more_plausible_reading(self):
        # W&T tags gas reserves as 423,300,000,000 ft3 -- 423.3 Bcf, correct --
        # under a header reading MMBoe. Both readings clear the band, but the
        # tagged one implies $9.23/BOE against the header's $1.54 at the edge.
        # Ranking the document first took the edge reading and multiplied the
        # reserve base sixfold.
        from basin.facts.scale import resolve

        r = resolve(423_300_000_000.0, "ft3", 1e9, 651_300_000.0, None,
                    header_units=("MMBoe",))
        assert r.status == "resolved"
        assert r.reserve_unit == "ft3"
        assert not r.unit_corrected
        assert 8.0 < r.usd_per_boe < 11.0

    def test_a_reading_no_producer_could_hold_is_rejected(self):
        # 423.3e9 read as MMBoe is 4.2e17 BOE -- more than world reserves.
        from basin.facts.scale import resolve

        r = resolve(423_300_000_000.0, "ft3", 1e9, 651_300_000.0, None,
                    header_units=("MMBoe",))
        assert "implausible" in r.rejected
