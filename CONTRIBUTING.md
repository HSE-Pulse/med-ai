# Contributing

Thanks for your interest in `med-ai`. Contributions are welcome under the
project's [Apache-2.0 License](LICENSE) — by submitting a contribution you agree
it is licensed under those same terms.

## ⛔ The one hard rule: never commit data or PHI

This is a clinical-ML project built on credentialed MIMIC-IV data. **Do not
commit, attach, or include in a PR any of the following:**

- MIMIC-IV data or any derivative of it (CSV/parquet/npz, Mongo dumps, etc.)
- Trained model weights derived from MIMIC (`*.pt`, `*.joblib`, …)
- Screenshots, fixtures, logs, or notebook outputs that contain patient-level
  rows, notes, or identifiers
- Real `.env` files, credentials, API keys, or connection strings

The `.gitignore` already excludes the common offenders, but **you are
responsible** for checking your diff before pushing. When in doubt, leave it out.
See [docs/DATA_ACCESS.md](docs/DATA_ACCESS.md).

## Workflow

1. Open an issue describing the change before large work.
2. Branch, make focused commits, run `ruff` and `pytest`.
3. Keep new dependencies permissively licensed (MIT/BSD/Apache-2.0). Flag any
   copyleft or model/data with restrictive terms in the PR.
4. Submit a PR with a clear description and a confirmation that no data/PHI is
   included.

## Scope reminder

This is **research/educational software, not a medical device** — see
[DISCLAIMER.md](DISCLAIMER.md). Please keep that framing in code, docs, and UI.
