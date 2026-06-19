# Architecture Notes

These notes capture the repo's durable design decisions. They are intentionally short:
implementation details live in the code and command docs, while these records explain why the
system is shaped this way.

- [ADR 0001: Raw Evidence Is the Source of Rebuild Truth](adr/0001-raw-evidence-source-of-truth.md)
- [ADR 0002: Fetch Delta Windows and Checkpoints](adr/0002-fetch-delta-checkpoints.md)
- [ADR 0003: Changelog Stores Compact Event History](adr/0003-changelog-event-history.md)
- [ADR 0004: Validation Is a Private Extension Contract](adr/0004-validation-extension-contract.md)
