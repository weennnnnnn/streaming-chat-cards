# AI usage log

This project was built with heavy AI assistance, per the brief's allowance for
AI tooling. This log documents how it was used, which parts were AI-generated,
and how the human in the loop steered and verified the work.

## Tools used

- **WorkBuddy** (AI coding agent, Anthropic Claude-based): the primary tool for
  the entire session below — design discussion, implementation, testing, and
  commit hygiene.
- Usage statistics (daily heatmap) for the last 3 months: see
  [`usage-heatmap/`](usage-heatmap/) (screenshots to be added by the author).

## How the work was done

The development session followed this flow (dates: 2026-11-20):

1. **Requirement analysis.** The candidate shared the task brief with the
   assistant and asked it to break down the requirements, identify the hard
   constraints (commit discipline, resumable event stream, loop/connection
   decoupling, the three behavioral rules) and produce a step-by-step
   implementation plan organized around the required commit history.
2. **Plan review.** The human reviewed the plan, then created the GitHub
   repository themselves and provided the remote URL.
3. **Implementation with verification.** The assistant implemented backend and
   frontend incrementally. Every step was verified before commit:
   - backend: an end-to-end smoke test (`scripts/smoke_test.py`) driving the
     live SSE stream — happy path ordering, `409` on concurrent sends, reject,
     mid-generate cancel, forced image failure (error + no retry), and
     full-log replay ordering (18/18 checks passing)
   - frontend: `tsc --noEmit` (strict) + `vite build`, dev-server proxy checks
4. **Human checkpoints.** The human created the repo, pushed credentials-side
   commits, and will run the final demo + recording themselves.
5. **Commit discipline.** Small, sequential, meaningful commits, each pushed
   immediately (see `git log`): docs → backend core → tests → frontend →
   launcher → docs. No history rewriting.

## What was AI-generated vs. human

| Artifact | AI | Human |
| --- | --- | --- |
| Implementation plan / design doc | written | reviewed, adjusted |
| Backend code (`backend/`) | written | reviewed |
| Frontend code (`frontend/`) | written | reviewed |
| Smoke test (`scripts/smoke_test.py`) | written | executed |
| GitHub repo creation & pushes | — | done (credentials) |
| Final demo recording | — | done |
| Usage heatmap screenshots | — | captured |

## Honest caveats

- The "search" step and the "image generation" step are intentionally mocked
  per the brief; the SVG placeholder is deterministic by prompt hash.
- The session store is in-memory and single-session, as scoped; the sequence
  contract is designed so a durable store (Redis stream / Postgres) can be
  dropped in without changing clients.
