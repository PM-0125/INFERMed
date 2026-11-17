# Contributing to INFERMed

Contributions can improve retrieval, evidence interpretation, tests, documentation, and the user interface. Keep each pull request focused on a specific problem. Discuss substantial changes in an issue before implementing them.

## License and contribution rights

INFERMed is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE). It permits use, modification, and distribution for the purposes defined in that license, including noncommercial research, education, evaluation, and development. It also expressly permits use by the organizations described in its Noncommercial Organizations section.

By intentionally submitting a contribution for inclusion, you offer it under the repository license. You retain your copyright and must have the right to submit the work, including any necessary employer permission. Identify third-party material and preserve its license and attribution. Do not submit material with incompatible terms.

Contributing does not transfer copyright or automatically authorize maintainers to commercially relicense another contributor's work. Any separate commercial agreement must cover the necessary rights from the relevant rights holders. Commercial use outside the permissions of LICENSE requires a separate written agreement. Contact Pranjul Mishra at [pranjul.mishra@proton.me](mailto:pranjul.mishra@proton.me) with the intended use and organization. Submitting a request does not grant permission.

## Reporting a problem

Use a GitHub issue for reproducible bugs, documentation problems, and proposed improvements. Include:

- The version or commit and steps to reproduce the problem.
- Medication names, audience mode, and relevant settings, using synthetic patient details.
- Whether evidence was refreshed or read from a cache.
- Expected and observed behavior, with a small redacted example where useful.
- Relevant source links and passages when questioning an evidence-based statement.

Do not include credentials, identifiable patient information, or restricted source material. Send sensitive security or privacy reports privately to the contact above rather than posting them in an issue. Community behavior reports follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

Follow [local development](docs/local_development.md) for runtime configuration and data setup. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
```

For frontend work:

```powershell
cd frontend
npm ci
```

Keep credentials in local configuration. Do not commit `.env` files or personal deployment settings.

## Evidence and data

- Preserve source identity, drug scope, units, qualifications, and uncertainty when changing retrieval or explanations.
- Do not turn spontaneous adverse-event reports into causal claims or incidence estimates. Single-drug evidence is not automatically evidence of an interaction.
- Keep study measurements separate from patient facts. Missing information must not become a negative finding.
- Do not commit bulk datasets, runtime caches, generated responses, or licensed/private data. A Git LFS pointer does not establish redistribution permission.
- Small test fixtures are welcome when redistribution is permitted and their provenance is documented. Use synthetic data where practical without removing the structure needed to reproduce a defect.
- The code license does not grant rights to external datasets, publications, APIs, or models. Check [NOTICE.md](NOTICE.md) and [data_license.md](data_license.md).

## Verification

For a bug fix, add or update a test that reproduces the failure where feasible. Use deterministic tests for normal development and clearly identify checks that require live services or local datasets. Do not contact external services or change runtime settings during test discovery.

Run checks relevant to your changes. Examples from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_source_integrity.py -q
.\.venv\Scripts\python.exe -m black --check src tests scripts
```

Replace the example test path with the affected tests. For broader Python changes, run the full suite with `.\.venv\Scripts\python.exe -m pytest tests -q` and document required services or datasets. For frontend changes, run these commands from `frontend`:

```powershell
npm test
npm run lint
npm run build
```

Inspect rendered output for interface changes. Report commands run, results, and checks you could not run. Mocked tests do not establish that a live API works, and software tests do not establish clinical validity.

## Pull requests

Explain the problem, resulting behavior, and verification. Link the relevant issue and identify compatibility changes or remaining limitations. Keep unrelated formatting and refactoring out of the pull request. Preserve useful tests and fixtures rather than removing them to obtain a passing result.

INFERMed is research software. Do not describe generated output or passing tests as clinical validation. Follow the [Code of Conduct](CODE_OF_CONDUCT.md) when discussing and reviewing contributions.
