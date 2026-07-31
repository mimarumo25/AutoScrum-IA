# Repository agent policy

## Mandatory engineering quality

All coding agents, including OpenCode and Codex, MUST read and follow
`ENGINEERING_QUALITY.md` before creating or modifying code. That document is the
canonical definition of done for unit tests, regression tests, SOLID review,
security, maintainability, scalability and fail-closed quality gates. Tool-specific
instructions may add constraints but may never weaken it.

No code change is complete until applicable tests and gates have been executed
and their exact results reported. Never bypass, disable or relax a failing test,
gate, linter, type check or security control to make a change pass.

This repository uses `codebase-memory` as the source of truth for understanding
the codebase. Apply the following policy to every future coding, debugging,
review, and architecture task in this repository.

## Mandatory graph-first workflow

When an answer or implementation requires facts about code, call the graph
tools before using text search:

1. `search_graph` — locate functions, classes, routes, modules, variables, or
   concepts by name or natural-language query.
2. `trace_path` — inspect callers, callees, dependencies, or data flow before
   changing behavior.
3. `get_code_snippet` — read the exact implementation after obtaining its
   qualified name from `search_graph`.
4. `query_graph` — use Cypher for multi-hop relationships, ownership, or
   architecture questions.
5. `get_architecture` — use for high-level structure, boundaries, layers, and
   hotspots.

Do not begin code discovery with `grep`, `rg`, globbing, or broad file dumps
when the information can be obtained from the graph. For every non-trivial
change, record enough graph evidence to understand impact before editing.

## Permitted fallbacks

Use `rg`/text search only for string literals, error messages, configuration
values, documentation, shell scripts, Dockerfiles, generated files, or when
the graph has no matching node. If the graph is stale or unavailable, state
that explicitly and use the safest available fallback.

## Graph freshness

After substantial code changes, refresh the project index with
`index_repository` in `full` mode and `persistence: true`. Generated project
outputs and caches may remain excluded. If local uncommitted files are not
represented by the graph backend, report that limitation rather than claiming
the graph is fully current.

## Verification

Before delivery, use graph queries to verify the changed symbols and their
relationships, then run proportional tests and syntax checks. Preserve user
changes and never stage or commit unrelated work merely to refresh the graph.
