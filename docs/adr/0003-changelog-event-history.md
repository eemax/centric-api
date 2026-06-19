# ADR 0003: Changelog Stores Compact Event History

Status: accepted

## Context

Users need to understand what changed in the local cache, who changed it, and when it changed,
without turning the database into a full payload archive.

## Decision

Changelog records compact event history and rollups. Payload snapshots are optional and are omitted
by default. The current changelog index tracks enough previous record state to detect scoped
changes after ingest, while read views query summary tables and event rows rather than raw evidence.

## Consequences

Changelog is for activity history, not forensic payload storage. Raw evidence remains the payload
truth, and changelog tables should stay compact enough for routine status, summary, actor, prune,
and CLI workflows.
