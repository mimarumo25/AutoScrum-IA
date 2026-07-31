# GitHub Copilot instructions

Before proposing or applying code, follow the complete canonical policy in
`ENGINEERING_QUALITY.md` and repository constraints in `AGENTS.md`.

- Every new or changed behavior requires unit tests.
- Every bug fix requires a regression test.
- Apply SOLID pragmatically and keep code maintainable, scalable and secure.
- Prefer cohesive refactoring over duplication or parallel sources of truth.
- Run applicable tests, lint, type, build and security gates; gates fail closed.
- Never suggest disabling, skipping or weakening a failing quality control.
- State verification evidence and residual risks with every completed change.
