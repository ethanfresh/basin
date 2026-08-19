"""Finding the document a disclosure is actually in.

A form's primary document is not reliably where its substance lives. An 8-K
announces guidance in an attached earnings release; a 40-F is frequently a cover
sheet whose reserve statements are in the attached Annual Information Form.
Fetching ``primaryDocument`` alone finds nothing and reads as a filer that
disclosed nothing -- which is the failure these tests pin down.
"""

from __future__ import annotations

import pytest

from basin.documents.locate import (
    MIN_SUBSTANTIVE_EXHIBIT_BYTES,
    annual_report_exhibits,
    earnings_exhibits,
    is_wrapper_form,
    substantive_exhibits,
)
from basin.store import connect
from basin.store.db import record_filing, upsert_company


class FakeClient:
    """Serves one filing index page, so the parser is exercised for real."""

    def __init__(self, rows: list[tuple[str, str, str, str]]):
        cells = "".join(
            f"<tr><td>{i}</td><td>{desc}</td>"
            f"<td><a href='/x'>{doc}</a></td><td>{typ}</td><td>{size}</td></tr>"
            for i, (desc, doc, typ, size) in enumerate(rows, 1)
        )
        self._body = f"<table>{cells}</table>"

    def get_text(self, url: str) -> str:
        return self._body


# Modelled on Cenovus's 2025 40-F: a 15KB cover sheet, the AIF and MD&A as
# large exhibits, and a tail of certifications and auditor consents.
FORTY_F_INDEX = [
    ("40-F", "cve-20251231_d2.htm", "40-F", "15395"),
    ("EX-99.1", "a2025annualinformationform.htm", "EX-99.1", "2286000"),
    ("EX-99.2", "a2025managementsdiscussion.htm", "EX-99.2", "2420000"),
    ("EX-99.4", "a2025supplementaryinformat.htm", "EX-99.4", "984000"),
    ("EX-99.5", "ex995ye2025ceo302certifica.htm", "EX-99.5", "6000"),
    ("EX-99.9", "ex999pwcconsentform-40xf.htm", "EX-99.9", "4000"),
    ("EX-97", "ex97clawbackpolicy.htm", "EX-97", "40000"),
]


class TestAnnualReportExhibits:
    def test_keeps_the_substantive_exhibits(self):
        got = annual_report_exhibits(FakeClient(FORTY_F_INDEX), "1", "a")
        assert got == [
            "a2025annualinformationform.htm",
            "a2025managementsdiscussion.htm",
            "a2025supplementaryinformat.htm",
        ]

    def test_drops_certifications_and_consents(self):
        # Numerous, always small, and never carrying a reserve disclosure.
        got = annual_report_exhibits(FakeClient(FORTY_F_INDEX), "1", "a")
        assert not any("certifica" in name or "consent" in name for name in got)

    def test_ignores_non_ex99_attachments(self):
        # A clawback policy is large enough to pass the size gate and is still
        # not an annual report exhibit.
        assert "ex97clawbackpolicy.htm" not in annual_report_exhibits(
            FakeClient(FORTY_F_INDEX), "1", "a"
        )

    def test_never_returns_the_cover_sheet(self):
        # The whole point: Cenovus's primary document has no reserves in it.
        assert "cve-20251231_d2.htm" not in annual_report_exhibits(
            FakeClient(FORTY_F_INDEX), "1", "a"
        )

    def test_threshold_is_adjustable(self):
        got = annual_report_exhibits(FakeClient(FORTY_F_INDEX), "1", "a", min_bytes=1)
        assert len(got) == 5  # every EX-99, certifications included

    @pytest.mark.parametrize("size", ["0", "", "n/a"])
    def test_unparseable_size_is_excluded_not_crashed(self, size):
        rows = [("EX-99.1", "aif.htm", "EX-99.1", size)]
        assert annual_report_exhibits(FakeClient(rows), "1", "a") == []

    def test_earnings_exhibits_still_take_small_attachments(self):
        # An earnings release can be short; only the 40-F path filters on size,
        # because only there is the noise both large in number and always tiny.
        rows = [("EX-99.1", "release.htm", "EX-99.1", "9000")]
        assert earnings_exhibits(FakeClient(rows), "1", "a") == ["release.htm"]
        assert annual_report_exhibits(FakeClient(rows), "1", "a") == []


class TestRecordFilingFillsBlanks:
    @pytest.fixture
    def conn(self, tmp_path):
        conn = connect(tmp_path / "t.db")
        with conn:
            upsert_company(conn, "0000000001", "Test Corp", ticker="TST")
        return conn

    def test_a_later_writer_supplies_the_primary_document(self, conn):
        # ingest_xbrl sees an accession on a fact and knows no filename;
        # fetch_filings reads the submissions index and does.
        with conn:
            record_filing(conn, "0001-24-1", "0000000001", "40-F", "2026-02-19")
            record_filing(conn, "0001-24-1", "0000000001", "40-F", "2026-02-19",
                          primary_doc="cve-20251231_d2.htm", period_end="2025-12-31")
        row = conn.execute("SELECT * FROM filing WHERE accession = '0001-24-1'").fetchone()
        assert row["primary_doc"] == "cve-20251231_d2.htm"
        assert row["period_end"] == "2025-12-31"

    def test_a_known_value_is_never_overwritten(self, conn):
        with conn:
            record_filing(conn, "0001-24-2", "0000000001", "40-F", "2026-02-19",
                          primary_doc="real.htm")
            record_filing(conn, "0001-24-2", "0000000001", "40-F", "2026-02-19",
                          primary_doc="wrong.htm")
        row = conn.execute("SELECT * FROM filing WHERE accession = '0001-24-2'").fetchone()
        assert row["primary_doc"] == "real.htm"


class TestWrapperForms:
    """Forms whose primary document is a cover sheet."""

    @pytest.mark.parametrize("form", ["40-F", "40-F/A", "6-K", "8-K", "8-K/A"])
    def test_wrapper_forms_are_recognised(self, form):
        assert is_wrapper_form(form)

    @pytest.mark.parametrize("form", ["10-K", "10-Q", "20-F", None])
    def test_self_contained_forms_are_not(self, form):
        # A 20-F carries its reserve disclosure in the primary document --
        # checked against Shell, BP, Equinor and Petrobras -- so it needs no
        # exhibit fetch and should not pay for one.
        assert not is_wrapper_form(form)


class TestSubstantiveExhibits:
    # A 6-K interim results release: one real attachment, plus noise.
    SIX_K_INDEX = [
        ("6-K", "form6k.htm", "6-K", "9000"),
        ("EX-99.1", "results.htm", "EX-99.1", "88000"),
        ("EX-99.2", "presentation.htm", "EX-99.2", "42000"),
        ("EX-99.3", "cert.htm", "EX-99.3", "1800"),
    ]

    def test_returns_largest_first(self):
        got = substantive_exhibits(FakeClient(self.SIX_K_INDEX), "1", "a")
        assert got == ["results.htm", "presentation.htm"]

    def test_floor_is_lower_than_the_annual_report_filter(self):
        # A 6-K results release can be a few kilobytes; the annual-report
        # filter would discard it while hunting for a 300KB AIF.
        rows = [("EX-99.1", "release.htm", "EX-99.1", "9000")]
        assert substantive_exhibits(FakeClient(rows), "1", "a") == ["release.htm"]
        assert annual_report_exhibits(FakeClient(rows), "1", "a") == []

    def test_certifications_are_still_excluded(self):
        got = substantive_exhibits(FakeClient(self.SIX_K_INDEX), "1", "a")
        assert "cert.htm" not in got

    def test_count_is_capped(self):
        # One verification must not turn into twenty fetches.
        rows = [(f"EX-99.{i}", f"d{i}.htm", f"EX-99.{i}", "50000") for i in range(20)]
        assert len(substantive_exhibits(FakeClient(rows), "1", "a")) == 6
        assert len(substantive_exhibits(FakeClient(rows), "1", "a", limit=2)) == 2
