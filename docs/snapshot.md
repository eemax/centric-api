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
uv run centric-api snapshot build dpp --target baseline
uv run centric-api snapshot promote dpp
uv run centric-api snapshot build dpp --output-dir ~/review-repos/snapshots
uv run centric-api snapshot build dpp --output-dir ~/review-repos/snapshots --clean
```

Private modules load from `CENTRIC_API_HOME/snapshots/*.py` by default. Override with
`--snapshots-dir PATH`.

Build output defaults to `CENTRIC_API_HOME/snapshot/workspaces/<snapshot-name>/candidate/`.
Override the workspace root with `--output-dir PATH`; the snapshot name and target are always
appended, so `--output-dir ~/snapshots` writes `~/snapshots/dpp/candidate` by default.

Use `--target candidate` for regenerated review output and `--target baseline` to rebuild the
approved main snapshot directly. Prefer `snapshot promote NAME` when approving reviewed output,
because it copies the existing `candidate/` artifacts into `baseline/` exactly instead of rebuilding
from a possibly changed cache. A later review UI can diff `baseline/` against `candidate/` and call
the same promotion path when approved.

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


SNAPSHOT = DppSnapshot()
```

Useful `ctx` helpers:

- `ctx.records(endpoint)`: cached endpoint payloads ordered by record ID.
- `ctx.records_any("new_name", "old_name")`: resolve the first cached endpoint from candidates.
- `ctx.index_by_id(endpoint)`: cached records keyed by `id`.
- `ctx.clean_ref(value)`: normalize empty Centric references.
- `ctx.refs(value)`: collect unique references from nested payload values.
- `ctx.record(stream, key, data, group=...)`: create a `SnapshotRecord`.

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
- `snapshot promote NAME` copies the selected snapshot's `candidate/` target to `baseline/`.
- `--clean` removes non-hidden contents in the selected target only and preserves hidden
  directories such as `.git`.
