## What this changes

<!-- Which services/<name> (or infra/) directory, and what/why. -->

## How it was verified

<!--
Per CONTRIBUTING.md: re-run the affected service's test suite from a
clean venv (its own README has the exact install sequence), and any
other service's suite whose shared dependency this touches.
-->

- [ ] Ran `services/<name>`'s own test suite from a clean venv install
- [ ] Ran the test suite of every other service that consumes this one
      as a local dependency (if any)
- [ ] No secret, credential, or `.env`-shaped value is included in this
      diff

## Checklist

- [ ] This change stays within one deliverable's directory (or is
      explicitly a cross-cutting change, explained above)
- [ ] Real code against a real local mock at any external boundary —
      no fabricated credential, no skipped integration pretending to be
      done
- [ ] Docs updated (that service's README / `CLAUDE.md` if a convention
      changed)
