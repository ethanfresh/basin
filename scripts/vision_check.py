"""Cross-check parser output against the rendered page, with a vision model.

    python scripts/vision_check.py --per-group 12

For a stratified sample of verified facts, render the sheet the fact was
located on, show the image to Claude, and ask what a reader sees: is the value
there, what column header governs it, what unit that header states, and what
page number is printed on the page. Store agreement per fact.

The sample is stratified where the parser is most likely to be wrong:

  no_header       the parser attached no table header (43% -> 25% of facts)
  unit_corrected  scale resolution overrode the tagged unit -- the corrections
                  themselves deserve checking, since one over-generalised
                  correction has already produced a 4.2e17 BOE reading
  control         parser found a header; the baseline agreement rate

Uses claude-opus-5 with structured output. Reading a table from an image is
deliberately run at low effort -- it is perception, not reasoning.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
from pathlib import Path

import anthropic

from basin.documents.render import render_png, sheet_html
from basin.store import DEFAULT_DB_PATH, connect

MODEL = "claude-opus-5"

SCHEMA = {
    "type": "object",
    "properties": {
        "value_present": {
            "type": "boolean",
            "description": "Whether the target number appears on the page",
        },
        "column_header": {
            "type": ["string", "null"],
            "description": "The column heading directly governing the target "
            "number, exactly as printed, or null if it is not in a table",
        },
        "row_label": {
            "type": ["string", "null"],
            "description": "The row label for the target number, or null",
        },
        "unit": {
            "type": ["string", "null"],
            "description": "The unit the page states for the target number "
            "(from its column header, a table caption, or adjacent text), "
            "e.g. Bcfe, MBbls, MMcf. Null if the page states none.",
        },
        "printed_page_number": {
            "type": ["integer", "null"],
            "description": "The page number printed on the page itself, "
            "usually at the bottom. Null if none is printed.",
        },
        "note": {
            "type": "string",
            "description": "One sentence on anything ambiguous",
        },
    },
    "required": [
        "value_present", "column_header", "row_label", "unit",
        "printed_page_number", "note",
    ],
    "additionalProperties": False,
}

SYSTEM = (
    "You are reading one page of an SEC filing, rendered exactly as filed. "
    "Answer only from what is visible on the page. If the same number appears "
    "more than once, use the occurrence in the table whose row label is "
    "closest to the given context. Report headers and units exactly as "
    "printed; do not normalise or infer units the page does not state."
)


def sample(conn, per_group: int) -> list[dict]:
    """Stratified sample of verified facts with a page to render."""
    base = """
        SELECT f.id, f.cik, f.concept_key, f.value, f.unit AS tagged_unit,
               f.accession, v.document, v.page, v.folio, v.printed,
               v.units_nearby, v.note AS parser_note, co.ticker
        FROM fact_verification v
        JOIN fact f ON f.id = v.fact_id
        JOIN company co ON co.cik = f.cik
        WHERE v.status = 'found' AND v.page IS NOT NULL
          AND v.printed IS NOT NULL AND v.document IS NOT NULL
    """
    groups = {
        "no_header": base + " AND v.units_nearby IS NULL ORDER BY f.id LIMIT ?",
        "unit_corrected": base + """ AND f.id IN (
                SELECT fact_id FROM fact_scale WHERE basis LIKE '%unit read as%')
            ORDER BY f.id LIMIT ?""",
        "control": base + """ AND v.units_nearby IS NOT NULL AND f.id NOT IN (
                SELECT fact_id FROM fact_scale WHERE basis LIKE '%unit read as%')
            ORDER BY f.id LIMIT ?""",
    }
    rows: list[dict] = []
    for name, sql in groups.items():
        for row in conn.execute(sql, (per_group,)):
            entry = dict(row)
            entry["sample_group"] = name
            rows.append(entry)
    return rows


def check_one(client: anthropic.Anthropic, fact: dict, image_path: Path) -> dict:
    image = base64.standard_b64encode(image_path.read_bytes()).decode()
    context = (
        f"Target number: {fact['printed']}\n"
        f"Context: this is reported as {fact['concept_key'].replace('_', ' ')} "
        f"for {fact['ticker'] or fact['cik']}."
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        output_config={
            "format": {"type": "json_schema", "schema": SCHEMA},
            "effort": "low",
        },
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image,
                    },
                },
                {"type": "text", "text": context},
            ],
        }],
    )
    if response.stop_reason == "refusal":
        return {"error": "refusal"}
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def header_agrees(parser_note: str | None, vision_header: str | None) -> bool | None:
    """Loose comparison: the parser stores 'column: X; row: Y' prose."""
    if not parser_note or not parser_note.startswith("column:"):
        return None
    parser_header = parser_note.split(";")[0].removeprefix("column:").strip()
    if not vision_header:
        return False
    a = "".join(parser_header.lower().split())
    b = "".join(vision_header.lower().split())
    return a in b or b in a


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--per-group", type=int, default=12)
    parser.add_argument("--recheck", action="store_true")
    args = parser.parse_args(argv)

    client = anthropic.Anthropic()
    conn = connect(args.store)
    facts = sample(conn, args.per_group)
    if not args.recheck:
        done = {r[0] for r in conn.execute("SELECT fact_id FROM vision_check")}
        facts = [f for f in facts if f["id"] not in done]
    print(f"checking {len(facts)} facts")

    tally: dict[str, list] = {}
    with tempfile.TemporaryDirectory(prefix="basin-vision-") as tmp:
        for n, fact in enumerate(facts, 1):
            html = sheet_html(fact["accession"], fact["document"], fact["page"])
            if html is None:
                continue
            image_path = Path(tmp) / f"{fact['id']}.png"
            try:
                render_png(html, image_path)
                result = check_one(client, fact, image_path)
            except (anthropic.AuthenticationError, TypeError) as exc:
                # The SDK raises TypeError("Could not resolve authentication
                # method...") before any HTTP call when no credential source
                # exists. Either way this dooms every remaining fact, so stop
                # rather than recording the whole sample as errors.
                if "authentication" not in str(exc).lower() and not isinstance(
                    exc, anthropic.AuthenticationError
                ):
                    raise
                print(
                    "\nNo API credentials. Set ANTHROPIC_API_KEY or run "
                    "`ant auth login`, then re-run.",
                    file=sys.stderr,
                )
                return 1
            except Exception as exc:  # render or API failure: record and move on
                result = {"error": str(exc)[:200]}

            if "error" in result:
                conn.execute(
                    """INSERT OR REPLACE INTO vision_check
                       (fact_id, sample_group, note, model) VALUES (?, ?, ?, ?)""",
                    (fact["id"], fact["sample_group"], result["error"], MODEL),
                )
                conn.commit()
                continue

            agree_h = header_agrees(fact["parser_note"], result["column_header"])
            agree_f = (
                None if result["printed_page_number"] is None and fact["folio"] is None
                else result["printed_page_number"] == fact["folio"]
            )
            conn.execute(
                """INSERT OR REPLACE INTO vision_check
                   (fact_id, sample_group, value_present, agree_header,
                    agree_folio, vision_header, vision_row, vision_unit,
                    vision_folio, parser_header, parser_folio, note, model)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fact["id"], fact["sample_group"],
                    int(bool(result["value_present"])),
                    None if agree_h is None else int(agree_h),
                    None if agree_f is None else int(agree_f),
                    result["column_header"], result["row_label"], result["unit"],
                    result["printed_page_number"],
                    fact["parser_note"], fact["folio"],
                    result["note"], MODEL,
                ),
            )
            conn.commit()
            tally.setdefault(fact["sample_group"], []).append(result)
            print(f"  [{n}/{len(facts)}] {fact['ticker'] or '-'} {fact['printed']:>14} "
                  f"({fact['sample_group']})  header={result['column_header']!r} "
                  f"unit={result['unit']!r} folio={result['printed_page_number']}",
                  flush=True)

    print("\nsummary:")
    for row in conn.execute("""
        SELECT sample_group, COUNT(*) n,
               SUM(value_present) present,
               SUM(agree_header) header_ok, COUNT(agree_header) header_n,
               SUM(agree_folio) folio_ok, COUNT(agree_folio) folio_n
        FROM vision_check WHERE value_present IS NOT NULL GROUP BY sample_group"""):
        print(f"  {row['sample_group']:<16} n={row['n']:<4} value present {row['present']}/{row['n']}"
              f"  header agrees {row['header_ok']}/{row['header_n']}"
              f"  folio agrees {row['folio_ok']}/{row['folio_n']}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
