# ADR 0002: Fetch Delta Windows and Checkpoints

Status: accepted

## Context

Fetch runs need to be resumable and efficient across large endpoints. Delta fetches also need a
conservative overlap so records modified during a previous run are not missed.

## Decision

`fetch` defaults to delta mode. Delta floors come from `delta.yml` and use overlap windows from the
previous successful fetch start. Explicit `--full`, `--days`, and `--months` modes remain separate
operator choices. Endpoint fetches write checkpoints during pagination, and resumed runs continue
from checkpointed windows unless an integrity failure marks the checkpoint for restart.

## Consequences

The endpoint fetcher is a state machine: checkpoint state, output append mode, page iteration,
count validation, ID validation, and final checkpoint writes are tightly coupled. Refactors should
preserve that locality unless a change makes the state transitions simpler and better tested.
