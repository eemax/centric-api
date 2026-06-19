from __future__ import annotations

from ._raw.check import check_raw_run, check_raw_runs
from ._raw.common import (
    RawCheckFile,
    RawCheckResult,
    RawCompactResult,
    RawIndexBuildResult,
    RawIndexResult,
    RawIndexRunResult,
    RawObservation,
    canonical_json,
    iter_raw_index,
    load_raw_manifest,
    raw_index_path,
    read_raw_index,
    resolve_raw_run_path,
    sha256_text,
)
from ._raw.compact import compact_raw_runs
from ._raw.index import build_raw_index, index_raw_runs, raw_index_manifest_fields
from ._raw.observe import (
    choose_diff_observations,
    diff_payloads,
    find_raw_observations,
    load_observation_payload,
    load_observation_payloads,
)

for _result_type in (
    RawCheckFile,
    RawCheckResult,
    RawCompactResult,
    RawIndexBuildResult,
    RawIndexResult,
    RawIndexRunResult,
    RawObservation,
):
    _result_type.__module__ = __name__
del _result_type

__all__ = [
    "RawCheckFile",
    "RawCheckResult",
    "RawCompactResult",
    "RawIndexBuildResult",
    "RawIndexResult",
    "RawIndexRunResult",
    "RawObservation",
    "build_raw_index",
    "canonical_json",
    "check_raw_run",
    "check_raw_runs",
    "choose_diff_observations",
    "compact_raw_runs",
    "diff_payloads",
    "find_raw_observations",
    "index_raw_runs",
    "iter_raw_index",
    "load_observation_payload",
    "load_observation_payloads",
    "load_raw_manifest",
    "raw_index_manifest_fields",
    "raw_index_path",
    "read_raw_index",
    "resolve_raw_run_path",
    "sha256_text",
]
