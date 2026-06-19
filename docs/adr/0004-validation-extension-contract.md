# ADR 0004: Validation Is a Private Extension Contract

Status: accepted

## Context

Validation logic is business-specific and often private, but the main toolkit should provide a
stable way to run validators, preflight cache requirements, and write consistent artifacts.

## Decision

The public repo owns the validation command, contracts, artifact writer, and history renderer.
Private validators live under `CENTRIC_API_HOME/validators` or a supplied `--validators-dir`, expose
a `VALIDATOR`, declare required endpoints, and return `ValidationResult` objects with summaries,
findings, sheets, optional report workbooks, and optional history metrics.

## Consequences

The core package can evolve artifact and history behavior without embedding private business rules.
Validators should keep domain logic private while relying on the shared contract for execution,
reporting, preflight checks, and trend history.
