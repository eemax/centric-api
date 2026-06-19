# Snapshots

`centric-api snapshot` builds deterministic JSONL directories from the local SQLite cache. Snapshot
logic is private Python code; the shared repo only provides module discovery, cache helpers,
validation, and artifact writing.

Snapshots are meant for Git review workflows: build modeled/grouped JSONL, review the diff, approve
or reject it in whatever UI or process sits on top later.

## Commands

```bash
uv run centric-api snapshot list
uv run centric-api snapshot show dpp
uv run centric-api snapshot check dpp
uv run centric-api snapshot build dpp
uv run centric-api snapshot diff dpp --review-file review.json
uv run centric-api snapshot promote dpp --review-file review.json
uv run centric-api snapshot build dpp --target baseline
uv run centric-api snapshot promote dpp --yes
uv run centric-api snapshot build dpp --output-dir ~/review-repos/snapshots
uv run centric-api snapshot build dpp --output-dir ~/review-repos/snapshots --clean
```

Private modules load from `CENTRIC_API_HOME/snapshots/*.py` by default. Override with
`--snapshots-dir PATH`.

Build output defaults to `CENTRIC_API_HOME/snapshot/workspaces/<snapshot-name>/candidate/`.
Override the workspace root with `--output-dir PATH`; the snapshot name and target are always
appended, so `--output-dir ~/snapshots` writes `~/snapshots/dpp/candidate` by default.

Use `--target candidate` for regenerated review output and `--target baseline` to rebuild the
approved main snapshot directly. Prefer `snapshot promote NAME --yes` when approving all reviewed
output, because it copies the existing `candidate/` artifacts into `baseline/` exactly instead of
rebuilding from a possibly changed cache. Full candidate-to-baseline promotion requires `--yes`;
selective `--review-file` promotion does not.

Use `snapshot diff NAME` to compare `baseline/` against `candidate/` without requiring a separate Git
checkout. Add `--review-file review.json` to write every current change as a review action. The
review file includes `schema_version: 1` and starts with all actions set to `skip`; change selected
actions to `promote`, then run `snapshot promote NAME --review-file review.json` to apply only those
approved changes to `baseline/`.

Review actions always keep raw `old` and `new` values as the promotion truth. When a private
snapshot policy knows how to present a record, `snapshot diff` writes `record_display` for the action
subject and `display` on impacted owner records. When the policy also knows how to resolve a changed
reference path, it writes `old_display` and `new_display` objects with the referenced endpoint, id,
and label. Every changed field that is part of an action is also listed in `field_diffs` with raw
`old` / `new` values and any available display values, which lets review UIs render record-level
actions without reopening the snapshot artifacts. Those display fields are for review tools and
humans only; selective promotion ignores them. `snapshot diff` uses the default cache DB for
reference display hydration when it exists, or use `--db PATH` to point at a specific cache.

The generic diff engine promotes changed fields by default. A private snapshot can hide
non-reviewable diagnostic paths from the diff, opt specific streams into record-level promotion, mark
fields as locked, and report impacted owner records. Locked fields cannot be promoted directly from
the review file. If all impacted owner records are promoted, the locked field can move automatically
with its owner; if only some impacted owners are promoted, the run fails instead of creating a mixed
baseline. When a locked change impacts an owner record that has no data diff of its own, the review
file includes an `owner_approval` action for that owner so UIs can still approve the locked change
selectively.

## Output Shape

The shared writer produces deterministic JSONL streams and a deterministic `manifest.json`.

For the private DPP snapshot, the intended first output shape is:

```text
dpp/
  baseline/
    manifest.json
    <Concept>/
      <Season>/
        <Brand>/
          style-boms.jsonl
          materials.jsonl
  candidate/
    manifest.json
    <Concept>/
      <Season>/
        <Brand>/
          style-boms.jsonl
          materials.jsonl
```

The default grouping convention is:

```text
concept / season / brand
```

Every JSONL row gets a stable `_key`. Rows are sorted by `_key`, JSON keys are sorted, and the
manifest avoids volatile values such as wall-clock build time so unchanged cache data does not create
noisy Git diffs.

## Private Module Contract

A private module exposes `SNAPSHOT` or `get_snapshot()`.

```python
from centric_api.snapshot import SnapshotDefinition, SnapshotOutput


class DppSnapshot:
    definition = SnapshotDefinition(
        name="dpp",
        title="DPP Snapshot",
        required_endpoints=("styles", "boms", "bom_lines", "materials"),
        description="Modeled DPP review snapshot.",
        version="v1",
    )

    def build(self, ctx):
        records = []
        for style in ctx.records("styles"):
            group = (
                style.get("concept") or "Unknown",
                style.get("season") or "Unknown",
                style.get("brand") or "Unknown",
            )
            style_id = str(style["id"])
            records.append(
                ctx.record(
                    "style-boms",
                    f"style-bom:{style_id}",
                    {"style_id": style_id, "node_name": style.get("node_name")},
                    group=group,
                )
            )
        return SnapshotOutput(tuple(records), metrics={"styles": len(records)})

    def diff_policy(self):
        return MyPrivateSnapshotDiffPolicy()


SNAPSHOT = DppSnapshot()
```

Useful `ctx` helpers:

- `ctx.records(endpoint)`: cached endpoint payloads ordered by record ID.
- `ctx.records_any("new_name", "old_name")`: resolve the first cached endpoint from candidates.
- `ctx.index_by_id(endpoint)`: cached records keyed by `id`.
- `ctx.clean_ref(value)`: normalize empty Centric references.
- `ctx.refs(value)`: collect unique references from nested payload values.
- `ctx.record(stream, key, data, group=...)`: create a `SnapshotRecord`.

Optional diff policy hooks:

- `ignored_change_path(identity, path) -> bool`: hide diagnostic or trace-only fields from review
  diffs. If all changed paths on a record are ignored, no review action is emitted.
- `record_promotion_streams`: stream names promoted as whole records instead of individual fields.
- `locked_field_reason(identity, path) -> str | None`: mark fields that cannot be promoted directly.
- `approval_owner(identity, path) -> str | None`: name the stream that owns approval for a change.
- `impacts(change, baseline, candidate) -> tuple[SnapshotRecordIdentity, ...]`: report owner records
  affected by a locked change.

## Writer Rules

- Stream names become JSONL filenames. Use names like `style-boms` or `materials`.
- Group values become directories. Empty group values become `Unknown`; path separators are replaced,
  and leading dots are stripped so data cannot write hidden directories such as `.git`.
- Duplicate `(group, stream, key)` records fail the run.
- A conflicting `data["_key"]` fails the run. Usually omit `_key` and let the writer inject it.
- If the output directory has a previous `manifest.json`, only previously managed files are removed.
- If the output directory has no manifest and contains non-hidden files, build fails unless
  `--clean` is passed.
- `--target candidate|baseline` selects which workspace target to write. The default is
  `candidate`.
- `snapshot promote NAME --yes` copies the selected snapshot's `candidate/` target to `baseline/`.
- `snapshot diff NAME --review-file review.json` writes selectable review actions for the current
  `baseline/` vs `candidate/` drift.
- `snapshot promote NAME --review-file review.json` applies only review actions set to `promote`.
- `--clean` removes non-hidden contents in the selected target only and preserves hidden
  directories such as `.git`.
