# AGENTS.md

These instructions apply to humans and AI agents working in this repository.

## Read before editing

1. Read `docs/ARCHITECTURE.md`.
2. Read the current phase and exit gate in `docs/IMPLEMENTATION_PLAN.md`.
3. Check the working tree and preserve changes made by other contributors.
4. Work only on the assigned module or explicitly agreed integration surface.

## Architectural boundaries

- `domain/` must not import Streamlit, database drivers, filesystem helpers, or
  environment/secrets APIs.
- `ui/` must not contain SQL or call database drivers directly.
- `infrastructure/` implements interfaces owned by `application/` or `domain/`;
  it must not contain Streamlit rendering.
- Legacy repositories are references and migration sources, never imported as
  production dependencies.
- Raw measurements are immutable after a committed import. Corrections create a
  new import or analysis revision with provenance.
- Database migrations are append-only. Never edit an applied migration.
- Do not run schema migrations automatically during ordinary Streamlit reruns.
- Do not commit real credentials, `.streamlit/secrets.toml`, real laboratory
  databases, identifiable experimental data, build artifacts, or virtual
  environments.

## Required workflow

- Add or update tests with every behavioral change.
- Run the phase-appropriate checks documented in `docs/IMPLEMENTATION_PLAN.md`.
- Keep public functions typed and document non-obvious invariants.
- Use structured domain exceptions rather than displaying UI messages from lower
  layers.
- Update architecture or migration documentation when contracts change.
- Prefer small, reviewable commits that leave the repository runnable.

## Parallel work rules

- One owner per file or module during a parallel wave.
- The root/integration owner controls shared contracts, `pyproject.toml`, schema
  migrations, and cross-module refactors unless ownership is reassigned.
- Parallel workers may not independently change a frozen interface. Propose the
  change to the integration owner first.
- Never revert another worker's edits. Adapt to the current tree and report
  conflicts.
- Integrate and run the full check suite after every parallel wave.

## Definition of done

A task is done only when:

- its acceptance checks pass;
- failure behavior is tested where material;
- no secrets or real data were introduced;
- relevant documentation is current; and
- the next developer can understand how to reproduce the result.
