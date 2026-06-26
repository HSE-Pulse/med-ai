# Data Access — MIMIC-IV

This repository contains **source code only**. It ships **no patient data**.
Every app reads from a local MongoDB populated with **MIMIC-IV**, which you must
obtain yourself under PhysioNet's credentialed license.

## Why no data is included

MIMIC-IV is released under the **PhysioNet Credentialed Health Data License
1.5.0**. That license **prohibits redistribution** of the data or of derivative
data that could contribute to re-identification. Trained model weights and
generated `datasets/*.parquet` files are derivatives of MIMIC and are therefore
**also excluded** from this repo (see `.gitignore`). This is the standard,
PhysioNet-permitted way to share clinical-ML code: ship the code, have each user
bring their own credentialed access.

## How to get access

1. Create a PhysioNet account: https://physionet.org/
2. Complete CITI "Data or Specimens Only Research" training and submit the
   completion report to PhysioNet.
3. Sign the MIMIC-IV Data Use Agreement (DUA).
4. Download MIMIC-IV: https://physionet.org/content/mimiciv/
   (and MIMIC-IV-Note for the discharge summaries, if using the NLP apps).

## Loading data into MongoDB

Once you have credentialed access to the raw MIMIC-IV CSVs, use the ingestion
scripts under `scripts/` to load them into a local MongoDB. The platform expects
these databases (collection counts approximate):

| Database | Contents |
|---|---|
| `MIMIC` | hosp module — admissions, transfers, labevents, diagnoses_icd, prescriptions, patients, … |
| `MIMIC_ICU` | icu module — icustays, chartevents, d_items, datetimeevents, ingredientevents |
| `MIMIC_Clinical_Notes` | discharge summaries (from MIMIC-IV-Note) |

Configure the connection with the `MONGO_URI` environment variable (see
`.env.example`).

## Item-ID / schema constants

`shared/constants/mimic.py` contains MIMIC-IV **item-ID → concept** mappings
(e.g. `220045 = Heart Rate`). These are schema/dictionary references — part of
MIMIC's published documentation, not patient data — and are included so the code
is runnable once you have your own data.

> ⚠️ Do not commit any MIMIC-derived data, model weights, screenshots of
> patient-level views, or `.mongo-data/` to a fork of this repository.
