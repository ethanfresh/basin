"""Shared fixtures.

Tests run offline. The payload below is trimmed from a real ``companyfacts``
response so it exercises the shapes the live run actually produced: the same
concept reached under two different taxonomies, multiple units on one tag, and
observations from forms Basin does not want.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def companyfacts() -> dict:
    return {
        "cik": 1090012,
        "entityName": "TEST ENERGY CORP",
        "facts": {
            "srt": {
                "ProvedDevelopedReservesBOE1": {
                    "units": {
                        "MMBoe": [
                            {
                                "start": None,
                                "end": "2024-12-31",
                                "val": 1200.0,
                                "accn": "0001090012-25-000010",
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-02-20",
                            },
                            {
                                "end": "2023-12-31",
                                "val": 1100.0,
                                "accn": "0001090012-24-000010",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-02-20",
                            },
                            {
                                # 10-Q observation: filtered out by default.
                                "end": "2024-06-30",
                                "val": 1150.0,
                                "accn": "0001090012-24-000020",
                                "fy": 2024,
                                "fp": "Q2",
                                "form": "10-Q",
                                "filed": "2024-08-01",
                            },
                        ]
                    }
                },
            },
            "us-gaap": {
                # Also present, but srt is listed first in the alias order, so
                # this must NOT win for the reserves concept.
                "ProvedDevelopedReservesBOE1": {
                    "units": {"Boe": [
                        {
                            "end": "2024-12-31",
                            "val": 1_200_000_000.0,
                            "accn": "0001090012-25-000010",
                            "form": "10-K",
                            "filed": "2025-02-20",
                        }
                    ]}
                },
                "PaymentsToAcquireOilAndGasProperty": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 3_500_000_000.0,
                                "accn": "0001090012-25-000010",
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-02-20",
                            },
                            {
                                # No accession: not citable, so not a fact.
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 3_100_000_000.0,
                                "form": "10-K",
                                "filed": "2024-02-20",
                            },
                        ]
                    }
                },
            },
        },
    }
