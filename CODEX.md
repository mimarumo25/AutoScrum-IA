# Codex repository instructions

Codex must follow `AGENTS.md` and the canonical policy in
`ENGINEERING_QUALITY.md` for every code creation or modification.

Required behavior:

- Add or update unit tests for every behavior change.
- Add a regression test for every bug fix.
- Review SOLID, maintainability, scalability and security.
- Run all applicable fail-closed quality gates and report exact results.
- Never weaken tests, gates, linters, type checks or security controls.
- Do not declare completion while required verification is failing or missing.
