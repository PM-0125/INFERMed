# Contributing to INFERMed

Thank you for contributing. INFERMed combines retrieval, pharmacology evidence, AI summarization, and a review-oriented frontend, so contributions should prioritize correctness, provenance, and safety.

## Ground Rules

- Do not commit `.env`, API keys, credentials, private health information, or private notes.
- Do not commit licensed data unless the repository explicitly permits redistribution.
- Keep medical claims evidence-scoped. If a claim comes from general pharmacology knowledge rather than retrieved evidence, label it clearly.
- Prefer small, reviewable pull requests with tests or a clear verification note.
- Preserve source provenance and caveats when changing retrieval or prompt behavior.

## Local Setup

Use the project virtual environment rather than global Python:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Frontend setup:

```powershell
cd frontend
npm install
npm run build
```

## Data Policy

The `data/` directory may contain public-safe demo/runtime artifacts. Licensed or restricted files must stay out of Git. In particular:

- `data/duckdb/drugbank.parquet` is ignored and must not be committed.
- Large binary data artifacts are tracked with Git LFS when committed.
- Generated caches should only be committed when they are intentionally part of the public demo/runtime state.

## Development Checks

Before opening a pull request, run the checks relevant to your change:

```powershell
python -m compileall src
pytest
cd frontend
npm run lint
npm run build
```

If a check cannot be run, mention why in the pull request.

## Pull Request Checklist

- The change is scoped to a clear problem.
- No secrets or restricted data are included.
- Retrieval, evidence, and prompt changes preserve uncertainty and source boundaries.
- Frontend changes are tested at desktop and narrow widths when layout is affected.
- New behavior is covered by tests or by a documented manual verification.

## Reporting Issues

When reporting a bug, include:

- The drug pair or workflow tested.
- Audience mode.
- Whether cached evidence was used or refreshed.
- Relevant terminal error, frontend behavior, or evidence panel observation.
- A redacted response sample if the issue concerns answer quality.
