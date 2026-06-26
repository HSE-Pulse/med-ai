/**
 * Client-side SOFA score calculation for the interactive SOFA Calculator.
 *
 * Extracted from SepsisIcu.tsx to enable reuse and reduce duplication.
 */

export function calcRespiratorySOFA(spo2: number): number {
  if (spo2 < 92) return 3;
  if (spo2 <= 96) return 1;
  return 0;
}

export function calcCoagulationSOFA(plt: number): number {
  if (plt < 20) return 4;
  if (plt < 50) return 3;
  if (plt < 100) return 2;
  if (plt < 150) return 1;
  return 0;
}

export function calcLiverSOFA(bili: number): number {
  if (bili > 12) return 4;
  if (bili >= 6) return 3;
  if (bili >= 2) return 2;
  if (bili >= 1.2) return 1;
  return 0;
}

export function calcCardioSOFA(mbp: number): number {
  return mbp < 70 ? 1 : 0;
}

export function calcRenalSOFA(cr: number): number {
  if (cr > 5) return 4;
  if (cr >= 3.5) return 3;
  if (cr >= 2) return 2;
  if (cr >= 1.2) return 1;
  return 0;
}

/**
 * CNS (Glasgow Coma Scale) — closes F15. The previous calculator omitted
 * CNS entirely, which systematically under-estimated total SOFA.
 *   GCS 15      → 0
 *   GCS 13–14   → 1
 *   GCS 10–12   → 2
 *   GCS 6–9     → 3
 *   GCS < 6     → 4
 */
export function calcCnsSOFA(gcs: number): number {
  if (gcs < 6) return 4;
  if (gcs < 10) return 3;
  if (gcs < 13) return 2;
  if (gcs < 15) return 1;
  return 0;
}

export interface SofaInputs {
  spo2: number;
  platelets: number;
  bilirubin: number;
  meanBp: number;
  creatinine: number;
  gcs: number;
}

export function calcTotalSOFA(input: SofaInputs): {
  components: number[];
  total: number;
} {
  const components = [
    calcRespiratorySOFA(input.spo2),
    calcCoagulationSOFA(input.platelets),
    calcLiverSOFA(input.bilirubin),
    calcCardioSOFA(input.meanBp),
    calcRenalSOFA(input.creatinine),
    calcCnsSOFA(input.gcs),
  ];
  return { components, total: components.reduce((a, b) => a + b, 0) };
}
