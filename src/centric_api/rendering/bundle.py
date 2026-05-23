from __future__ import annotations

from typing import Any

from ..bundle import BundleRunResult
from ..bundle_state import BundleComparison
from ..time_display import format_time_ago
from .common import format_count, signed_count


def print_human_bundle_summary(result: BundleRunResult) -> None:
    print("Bundle Complete")
    print()
    print(f"Job:       {result.bundle_name}")
    print(f"Download:  {result.download_job}")
    print(f"Run:       {result.run_id}")
    print(f"Manifest:  {result.manifest_path}")
    print(f"Changelog: {result.changelog_md_path}")
    if result.zip_path is not None:
        print(f"Zip:       {result.zip_path}")
    print()
    print("Summary")
    print(f"Items:     {result.item_count}")
    print(f"Added:     {result.added_count}")
    print(f"Changed:   {result.changed_count}")
    print(f"Renamed:   {result.renamed_count}")
    print(f"Removed:   {result.removed_count}")
    print(f"Unchanged: {result.unchanged_count}")
    print(f"Missing:   {result.missing_count}")


def print_human_bundle_list(rows: list[dict[str, Any]]) -> None:
    print("Bundle Runs")
    print()
    print(f"Runs: {format_count(len(rows))}")
    print()
    run_width = max(len("Run"), *(len(str(row["run_id"])) for row in rows))
    bundle_width = max(len("Bundle"), *(len(str(row["bundle_name"])) for row in rows))
    header = (
        f"{'Run':<{run_width}}  {'Finished':<12}  {'Bundle':<{bundle_width}}  "
        f"{'Items':>7}  {'Delta':>14}  Zip"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{str(row['run_id']):<{run_width}}  "
            f"{format_time_ago(row['finished_at']):<12}  "
            f"{str(row['bundle_name']):<{bundle_width}}  "
            f"{format_count(int(row['item_count'] or 0)):>7}  "
            f"{_bundle_delta_label(row):>14}  "
            f"{_zip_label(row.get('zip_path'))}"
        )


def print_human_bundle_show(run: dict[str, Any], items: list[dict[str, Any]]) -> None:
    print("Bundle Run")
    print()
    print(f"Run:       {run['run_id']}")
    print(f"Bundle:    {run['bundle_name']}")
    print(f"Download:  {run['download_job']}")
    print(f"Finished:  {format_time_ago(run['finished_at'])}")
    print(f"Zip:       {run.get('zip_path') or 'none'}")
    print()
    print("Summary")
    print(f"Items:     {run['item_count']}")
    print(f"Added:     {run['added_count']}")
    print(f"Changed:   {run['changed_count']}")
    print(f"Renamed:   {run['renamed_count']}")
    print(f"Removed:   {run['removed_count']}")
    print(f"Unchanged: {run['unchanged_count']}")
    if items:
        print()
        print("Files")
        rows = items[:50]
        change_width = max(len("Change"), *(len(str(item["change_type"])) for item in rows))
        document_width = max(len("Document"), *(len(str(item["document_id"])) for item in rows))
        revision_width = max(len("Revision"), *(len(str(item["revision_id"])) for item in rows))
        header = (
            f"{'Change':<{change_width}}  {'Document':<{document_width}}  "
            f"{'Revision':<{revision_width}}  Path"
        )
        print(header)
        print("-" * len(header))
        for item in rows:
            print(
                f"{str(item['change_type']):<{change_width}}  "
                f"{str(item['document_id']):<{document_width}}  "
                f"{str(item['revision_id']):<{revision_width}}  "
                f"{item['archive_path']}"
            )
        if len(items) > len(rows):
            print(f"... {len(items) - len(rows)} more")


def print_human_bundle_changelog(comparison: BundleComparison) -> None:
    summary = comparison.summary
    print("Bundle Changelog")
    print()
    print(f"Bundle: {comparison.from_run['bundle_name']}")
    print(f"From:   {comparison.from_run['run_id']}")
    print(f"To:     {comparison.to_run['run_id']}")
    print()
    print("Summary")
    print(f"Added:     {summary['added_count']}")
    print(f"Changed:   {summary['changed_count']}")
    print(f"Renamed:   {summary['renamed_count']}")
    print(f"Removed:   {summary['removed_count']}")
    print(f"Unchanged: {summary['unchanged_count']}")
    changed_items = [item for item in comparison.items if item["change_type"] != "unchanged"]
    if changed_items:
        print()
        print("Changes")
        for item in changed_items[:50]:
            print(f"- {item['change_type']}: {item['archive_path']}")
            if item["change_type"] == "renamed":
                print(f"  Previous path: {item.get('previous_archive_path') or 'unknown'}")
            elif item["change_type"] == "changed":
                print(f"  Previous revision: {item.get('previous_revision_id') or 'unknown'}")
                print(f"  Current revision: {item['revision_id']}")
        if len(changed_items) > 50:
            print(f"... {len(changed_items) - 50} more")


def bundle_record(result: BundleRunResult) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "bundle": result.bundle_name,
        "download_job": result.download_job,
        "manifest": str(result.manifest_path),
        "changelog_json": str(result.changelog_json_path),
        "changelog_md": str(result.changelog_md_path),
        "zip": str(result.zip_path) if result.zip_path else None,
        "item_count": result.item_count,
        "added_count": result.added_count,
        "changed_count": result.changed_count,
        "renamed_count": result.renamed_count,
        "removed_count": result.removed_count,
        "unchanged_count": result.unchanged_count,
        "missing_count": result.missing_count,
        "dry_run": result.dry_run,
    }


def bundle_comparison_record(comparison: BundleComparison) -> dict[str, Any]:
    return {
        "from_run": comparison.from_run,
        "to_run": comparison.to_run,
        "summary": comparison.summary,
        "items": list(comparison.items),
    }


def _bundle_delta_label(row: dict[str, Any]) -> str:
    pieces = [
        signed_count("+", int(row["added_count"] or 0)),
        signed_count("~", int(row["changed_count"] or 0)),
        signed_count("r", int(row["renamed_count"] or 0)),
        signed_count("-", int(row["removed_count"] or 0)),
    ]
    return " ".join(piece for piece in pieces if piece) or "none"


def _zip_label(value: Any) -> str:
    return "yes" if value else "none"
