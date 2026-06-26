# Models & Licenses

This platform uses two classes of model. **No model weights are committed** to
this repository (`models/` and `*.pt`/`*.joblib` are gitignored). This document
records what you will run and under which terms.

## 1. Locally-trained ML models (owned by this project)

Classical/deep models trained on MIMIC-IV — XGBoost, LightGBM, scikit-learn
(`.joblib`), and PyTorch (`.pt`) networks — for ED triage, sepsis, oncology,
etc. The training **code** is Apache-2.0 (this repo). The **weights** are
derivative works of MIMIC-IV and therefore are not distributed here; you
generate them by running the training scripts against your own MIMIC data.

## 2. Large Language Models (pulled at runtime via Ollama)

The clinical-chat / note-analysis features call local LLMs through Ollama. You
`ollama pull` these yourself; the platform only references the tags. **Three of
the four are source-available community licenses, NOT OSI-approved open source**,
and carry acceptable-use restrictions. Review each before any non-research use.

| Ollama tag | Base | License | Notes / restrictions |
|---|---|---|---|
| `llama3.2:3b` | Meta Llama 3.2 | **Llama 3.2 Community License** | "Built with Llama" attribution required; >700M-MAU clause; acceptable-use policy. Not OSI-open. |
| `deepseek-r1:8b` | DeepSeek-R1 distill (Llama-3.1-8B base) | **MIT** (distill) + **Llama 3.1 Community** (base) | MIT layer is clean; inherits Llama base restrictions. |
| `MedAIBase/MedGemma1.5:4b-it` | Google MedGemma / Gemma | **Gemma Terms of Use** + Health AI Developer Foundations | Prohibited-use policy; explicit **"not for clinical use"**. Not OSI-open. |
| `koesn/llama3-openbiollm-8b` | OpenBioLLM-8B (Llama-3-8B) | **Llama 3 Community License** | Research-oriented; medical-use disclaimers. |

> If you deploy this platform publicly or commercially, you are responsible for
> complying with each model's license and acceptable-use policy, including the
> **"Built with Llama"** attribution and the **"not for clinical use"** terms on
> MedGemma/OpenBioLLM. See [DISCLAIMER.md](../DISCLAIMER.md).

## Future / placeholder models

`app_09_waiting_list` and `app_10_clinical_scribe` reference Bio-ClinicalBERT
(`emilyalsentzer/Bio_ClinicalBERT`, MIT over BERT/Apache-2.0) as a Phase-2 item;
no `from_pretrained` call is wired today (current logic is keyword/regex).
