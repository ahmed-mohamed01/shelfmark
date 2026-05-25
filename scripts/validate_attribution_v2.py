"""Ad-hoc audit of v2 attributions against the live monitored DB.

  python scripts/validate_attribution_v2.py                # ./.local/config/users.db
  python scripts/validate_attribution_v2.py --db /path/to/users.db

For every existing row in ``monitored_book_files``, re-runs v2 against
(path, currently_attached_book) and reports rows v2 would reject. Also runs
``pick_best_attribution`` against all books of each entity and flags cases
where v2 prefers a different book.

Useful for spot-checking after a code change. Read-only, no DB writes.

All three sources (filesystem, ABS, Grimmory) run through the unified
``pick_best_attribution`` evaluator; this script re-runs that evaluator on
existing rows and flags ones it would no longer accept (or would prefer a
different book for).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from shelfmark.core.monitored_attribution_v2 import (  # noqa: E402
    evaluate_match,
    pick_best_attribution,
)

DEFAULT_DB = REPO_ROOT / ".local" / "config" / "users.db"


def _load(db_path: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        file_rows = [
            dict(r)
            for r in conn.execute("""
            SELECT
              f.id AS file_id, f.entity_id, e.name AS author_name,
              f.path, f.ext, f.file_type, f.source,
              f.confidence AS recorded_conf, f.match_reason,
              f.provider, f.provider_book_id, f.size_bytes,
              b.id AS book_id, b.title AS book_title,
              b.series_position, b.series_name,
              b.state, b.release_date,
              b.isbn_13, b.isbn_10, b.asins
            FROM monitored_book_files f
            JOIN monitored_entities e ON e.id = f.entity_id
            LEFT JOIN monitored_books b
              ON b.entity_id = f.entity_id
              AND b.provider = f.provider
              AND b.provider_book_id = f.provider_book_id
        """).fetchall()
        ]
        books = [
            dict(r)
            for r in conn.execute("""
            SELECT b.id AS book_id, b.entity_id, e.name AS author_name,
                   b.title, b.series_position, b.series_name, b.state,
                   b.release_date, b.isbn_13, b.isbn_10, b.asins,
                   b.provider, b.provider_book_id
            FROM monitored_books b
            JOIN monitored_entities e ON e.id = b.entity_id
        """).fetchall()
        ]
    finally:
        conn.close()
    return file_rows, books


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", type=str, default=str(DEFAULT_DB), help=f"Path to users.db (default: {DEFAULT_DB})"
    )
    args = parser.parse_args()

    db_path = args.db
    if not Path(db_path).is_file():
        print(f"error: DB file not found at {db_path}", file=sys.stderr)
        return 1

    print(f"Reading: {db_path}\n")
    file_rows, books = _load(db_path)

    books_by_entity: dict[int, list[dict]] = defaultdict(list)
    for b in books:
        books_by_entity[b["entity_id"]].append(b)

    rows_by_source: Counter = Counter()
    v2_accepts_existing: Counter = Counter()
    v2_rejects_existing: Counter = Counter()
    v2_better_choice: Counter = Counter()
    v2_same_choice: Counter = Counter()
    no_attached_book: Counter = Counter()

    rejected_high_conf_recorded: list[dict[str, Any]] = []
    different_best_choice: list[dict[str, Any]] = []

    for row in file_rows:
        src = row["source"]
        rows_by_source[src] += 1

        if row.get("book_id") is None:
            no_attached_book[src] += 1
            continue

        attached_book = {
            "title": row.get("book_title"),
            "series_position": row.get("series_position"),
            "series_name": row.get("series_name"),
            "isbn_13": row.get("isbn_13"),
            "isbn_10": row.get("isbn_10"),
            "asins": row.get("asins"),
            "provider": row.get("provider"),
            "provider_book_id": row.get("provider_book_id"),
        }

        # 1) Re-evaluate the existing attachment
        ev = evaluate_match(
            path=row["path"],
            book=attached_book,
            author_name=row["author_name"],
            embedded=None,
        )
        if ev.accept:
            v2_accepts_existing[src] += 1
        else:
            v2_rejects_existing[src] += 1
            if (row.get("recorded_conf") or 0.0) >= 0.85:
                rejected_high_conf_recorded.append(
                    {
                        "author": row["author_name"],
                        "book": row["book_title"],
                        "spos": row["series_position"],
                        "path": row["path"],
                        "src": src,
                        "recorded_conf": row.get("recorded_conf"),
                        "v2_net_score": ev.net_score,
                        "v2_confidence": ev.confidence,
                        "positives": [(p["name"], p["weight"]) for p in ev.positives],
                        "penalties": [(p["name"], p["weight"]) for p in ev.penalties],
                    }
                )

        # 2) Does v2 prefer a different book?
        entity_books = books_by_entity.get(row["entity_id"], [])
        result = pick_best_attribution(
            path=row["path"],
            books=entity_books,
            author_name=row["author_name"],
            embedded=None,
        )
        if result.book is None:
            continue
        if result.book.get("provider_book_id") == row.get("provider_book_id"):
            v2_same_choice[src] += 1
        else:
            v2_better_choice[src] += 1
            different_best_choice.append(
                {
                    "author": row["author_name"],
                    "currently_attached": row["book_title"],
                    "currently_spos": row["series_position"],
                    "currently_conf": row.get("recorded_conf"),
                    "v2_prefers": result.book.get("title"),
                    "v2_prefers_spos": result.book.get("series_position"),
                    "v2_confidence": result.confidence,
                    "path": row["path"],
                    "src": src,
                }
            )

    # ---------------- Report ----------------
    print("=" * 90)
    print("V2 ATTRIBUTION AUDIT")
    print("=" * 90)
    print(f"\nTotal file rows: {len(file_rows)}")
    print(f"\nBy source: {dict(rows_by_source)}\n")

    print("Per-source — v2 verdict on the EXISTING attribution:")
    for src in sorted(
        set(list(v2_accepts_existing) + list(v2_rejects_existing) + list(no_attached_book))
    ):
        total = rows_by_source[src]
        acc = v2_accepts_existing[src]
        rej = v2_rejects_existing[src]
        no_attached = no_attached_book[src]
        print(
            f"  {src:<14}  total={total:4d}  v2_accept={acc:4d}  v2_reject={rej:4d}  no_book={no_attached}"
        )

    print("\nPer-source — v2 best choice vs currently-attached book:")
    for src in sorted(set(list(v2_same_choice) + list(v2_better_choice))):
        print(
            f"  {src:<14}  v2_picks_same={v2_same_choice[src]:4d}  v2_picks_different={v2_better_choice[src]:4d}"
        )

    print(
        f"\nRows v2 rejects despite high recorded conf (>=0.85): {len(rejected_high_conf_recorded)}"
    )
    for x in rejected_high_conf_recorded[:30]:
        print(f"\n  [{x['src']}] {x['author']}  ->  '{x['book']}' (#{x['spos']})")
        print(f"    {x['path']}")
        print(
            f"    recorded={x['recorded_conf']:.2f}  v2_net={x['v2_net_score']:.2f}  v2_conf={x['v2_confidence']:.2f}"
        )
        print(f"    positives: {x['positives']}")
        print(f"    penalties: {x['penalties']}")
    if len(rejected_high_conf_recorded) > 30:
        print(f"\n  ... and {len(rejected_high_conf_recorded) - 30} more.")

    print(
        f"\n\nRows where v2 prefers a DIFFERENT book from what's currently attached: {len(different_best_choice)}"
    )
    for x in different_best_choice[:30]:
        print(f"\n  [{x['src']}] {x['author']}")
        print(
            f"    currently:    '{x['currently_attached']}' (#{x['currently_spos']}) at conf {x['currently_conf']:.2f}"
        )
        print(
            f"    v2 prefers:   '{x['v2_prefers']}' (#{x['v2_prefers_spos']}) at conf {x['v2_confidence']:.2f}"
        )
        print(f"    file: {x['path']}")
    if len(different_best_choice) > 30:
        print(f"\n  ... and {len(different_best_choice) - 30} more.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
