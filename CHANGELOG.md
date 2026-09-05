# Changelog

All notable changes to Med AI are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). While the major version is 0, minor
releases may contain breaking changes to service APIs or compose layouts.

Releases are cut with `scripts/release.sh <version>`; the version lives in the
top-level `VERSION` file (Python packaging reads it) and is mirrored into
`dashboard/package.json` (the dashboard footer shows it).

## [Unreleased]

## [0.3.0] - 2026-09-05

### Added
- Model source for every ML service under `app_NN/backend/models/`: training
  scripts, model wrappers and evaluation harnesses for ED triage, sepsis ICU,
  hospital ops (MARL), oncology, bed management, waiting list, clinical scribe
  and ED flow. A bare `models/` ignore pattern had kept this code out of git.
- Optional Langfuse tracing for the clinical chat LangGraph agent, enabled by
  the `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`
  environment variables (off when unset).
- MLflow tracking stack (`docker-compose.mlflow.yml`) with a read-only
  reverse proxy, and a training image (`docker/Dockerfile.train`).
- Grafana dashboards for end-to-end event flow and the OpenTelemetry collector.
- `docs/architecture.mmd` system diagram.
- Hospital ops MARL: `models/evaluate.py` policy benchmark, optional
  `staff_cost_weight` reward term, per-step curriculum advancement.
- Unit test for the one-bed-per-patient invariant.
- Release tooling: `VERSION`, `CHANGELOG.md`, `scripts/release.sh`, and a
  GitHub Actions workflow that publishes a Release for every `v*` tag.

### Changed
- Hospital ops DES splits queue wait from dwell time, backfills beds vacated
  through Kafka discharges, seeds capacities from bed management and
  reconciles its census against bed management every 30 s. The dashboard chart
  now plots queue wait only.
- Hospital ops suppresses the discharge-rate metric until an hour of engine
  time exists.
- MARL training batches critic and actor updates (shared centralised critic
  with per-agent heads), removing hundreds of per-step kernel launches.
- Unbounded in-process caches in sepsis ICU, clinical scribe and deterioration
  are now bounded.
- Dashboard: navigation reorder, Clinical Scribe patient-notes tab with note
  type filter, proxied Swagger UIs get a rewritten `openapi_url`, footer shows
  the release version.
- Python packaging now reads its version from `VERSION` and declares the
  Apache-2.0 licence that the repository ships.

### Fixed
- One bed per person: re-admitted subjects no longer receive a second bed;
  bed management refuses duplicate allocations and a dedupe sweep drains
  pre-existing duplicates.
- Clinical escalations (NEWS2/PEWS/IMEWS) no longer masquerade as capacity
  alerts, which had reset ward staffing and flooded the alert bus.
- Discharge-prediction models load inside containers (repo-relative
  `DATASET_DIR` default) instead of silently falling back to rules.
- Digital twin drops non-numeric lab and vital values (for example troponin
  `<0.01`) that caused schema-validation failures in discharge prediction.

## [0.2.0] - 2026-08-06

### Added
- Real-time Patient Flow page.
- Trolley Watch polls real HSE TrolleyGAR data daily across all six zones.
- Clinical chat plan/retrieve/verify pipeline over the whole estate.
- Observability stack: Prometheus alert scraping and console fixes.

### Fixed
- Cross-service data consistency in the digital twin; occupancy, bed
  inventory and waiting-list counts agree.
- ERP: EWTD endpoint, invented occupancy, department attribution of activity
  log entries, ICD codes and timestamps.
- Patient page no longer resolves URLs onto fabricated records.
- Scheduled journeys survive restarts; MARL observation corrected.
- Reset deadlock; `trolley_alert` has a real trigger.

## [0.1.0] - 2026-06-26

### Added
- Initial public release of Med AI under Apache-2.0: the clinical
  microservices, React dashboard, Kafka event backbone, digital twin
  simulation and observability compose stacks.

[Unreleased]: https://github.com/HSE-Pulse/med-ai/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/HSE-Pulse/med-ai/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/HSE-Pulse/med-ai/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/HSE-Pulse/med-ai/releases/tag/v0.1.0
