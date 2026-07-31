# ADR-001: Selection of programming language

## Status
Accepted

## Context
We need to implement an HTTP server with zero external dependencies. The language must provide a standard library HTTP server and a test runner.

## Decision
Use Python 3.8+.

## Alternatives considered
1. **Node.js (without npm packages)**
   - Built-in `http` module available, but the test runner (`node:test`) is experimental in Node 18+ and less mature. Node's `require` and ecosystem often leans on npm even for basic tooling.
   - Would force adoption of an unstable API for testing.
2. **Go**
   - Standard library `net/http` is robust, but compilation step adds complexity and Go's `testing` package requires structured projects.
   - Development overhead for such a minimal service outweighs benefits.

## Consequences
- Easy to implement, low ceremony.
- `unittest` is stable and included.
- Deployment needs only Python interpreter (no virtual environment required).

## Reversion condition
If Python's `http.server` proves too limited for production-grade performance (unlikely for this scope), we could revisit Node.js or Go.

## Cost estimate
$0/month – no licensing, open source.
