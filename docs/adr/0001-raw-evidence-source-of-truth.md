# ADR 0001: Raw Evidence Is the Source of Rebuild Truth

Status: accepted

## Context

Centric API fetches are operational evidence, not just temporary import files. Operators need to
inspect what was received, replay completed runs into SQLite, compare payload observations, and
recover from local database corruption without calling the API again.

## Decision

Completed raw JSONL runs under `CENTRIC_API_HOME/raw/runs` are the durable source of rebuild truth.
Fetches write into `raw/active`, promote successful runs into `raw/runs`, and quarantine failed runs
under `raw/failed`. Raw files carry sidecar indexes so inspection, diffing, verification, and
compaction can work without treating changelog tables as payload archives.

## Consequences

SQLite is a rebuildable cache. Recovery flows should replay completed raw evidence instead of
trusting current database state. Raw compaction must preserve the latest observable record state,
including deletes, and must remain explicit enough that operators can inspect the evidence chain.
