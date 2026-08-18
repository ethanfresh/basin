"""Document verification: does the value actually appear in the filing?

These are the checks that turn an accession from an assertion into a citation.
All offline — the HTML shapes here are copied from real filings.
"""

from __future__ import annotations

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
