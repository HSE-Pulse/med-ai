// Mirrors shared/constants/hospital.py::CAPACITIES — single source of truth
// for department bed counts. Must be kept in sync with the backend.
export const CAPACITIES: Record<string, number> = {
  ED: 30,
  MAU: 24,
  AMAU: 16,
  SAU: 12,
  CDU: 8,
  Medicine: 40,
  Surgery: 36,
  Cardiology: 20,
  Respiratory: 18,
  Orthopaedics: 24,
  ICU: 12,
  HDU: 8,
  Day_Ward: 20,
  Discharge_Lounge: 10,
};

export const DEPARTMENT_ORDER = [
  "ED", "MAU", "AMAU", "SAU", "CDU",
  "Medicine", "Surgery", "Cardiology", "Respiratory", "Orthopaedics",
  "ICU", "HDU", "Day_Ward", "Discharge_Lounge",
];
