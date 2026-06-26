/**
 * Irish HSE hospital arrival patterns per department.
 *
 * - hourly_profile: normalized probability of arrival at each hour (0-23), sums to ~1
 * - daily_profile: normalized probability by day-of-week (0=Mon), sums to ~1
 * - los_median_h / los_mean_h / los_p90_h: length-of-stay in hours
 * - total_transfers: estimated annual transfer volume
 *
 * Source: Irish HSE department configuration (14 departments).
 * Arrival profiles are modelled on typical Irish acute hospital patterns.
 */

export interface DeptArrivalProfile {
  hourly_profile: number[]; // 24 entries
  daily_profile: number[];  // 7 entries (Mon=0)
  los_median_h: number;
  los_mean_h: number;
  los_p90_h: number;
  total_transfers: number;
}

export const mimicArrivals: Record<string, DeptArrivalProfile> = {
  // ── Emergency Department ─────────────────────────────────────────────
  // Peaks afternoon 14-18, 24/7 operation, flat daily profile
  "ED": {
    hourly_profile: [0.0282,0.0249,0.0202,0.017,0.0143,0.013,0.0139,0.0165,0.023,0.036,0.0502,0.0642,0.0644,0.0637,0.0626,0.0658,0.0665,0.0643,0.061,0.0587,0.0525,0.0458,0.04,0.0332],
    daily_profile: [0.1435,0.1436,0.1417,0.142,0.1424,0.1431,0.1436],
    los_median_h: 6.7, los_mean_h: 8.2, los_p90_h: 14.0, total_transfers: 295854,
  },

  // ── Medical Assessment Unit ──────────────────────────────────────────
  // GP-referred acute medical patients, peaks 10-14:00, weekday-heavy
  "MAU": {
    hourly_profile: [0.012,0.010,0.008,0.007,0.006,0.006,0.008,0.015,0.035,0.062,0.078,0.082,0.080,0.075,0.068,0.060,0.055,0.050,0.045,0.040,0.035,0.028,0.020,0.015],
    daily_profile: [0.160,0.162,0.158,0.160,0.155,0.110,0.095],
    los_median_h: 18.0, los_mean_h: 22.0, los_p90_h: 48.0, total_transfers: 18200,
  },

  // ── Acute Medical Assessment Unit ────────────────────────────────────
  // Short-stay assessment, peaks 09-13:00, strongly weekday
  "AMAU": {
    hourly_profile: [0.008,0.006,0.005,0.005,0.004,0.005,0.008,0.018,0.045,0.072,0.085,0.088,0.082,0.075,0.065,0.058,0.052,0.048,0.042,0.038,0.032,0.025,0.018,0.012],
    daily_profile: [0.170,0.168,0.165,0.168,0.160,0.090,0.079],
    los_median_h: 12.0, los_mean_h: 14.0, los_p90_h: 36.0, total_transfers: 14500,
  },

  // ── Surgical Assessment Unit ─────────────────────────────────────────
  // Surgical referrals, peaks 08-16:00, weekday-heavy
  "SAU": {
    hourly_profile: [0.010,0.008,0.006,0.005,0.005,0.005,0.008,0.020,0.050,0.070,0.078,0.080,0.076,0.072,0.068,0.065,0.058,0.050,0.042,0.038,0.032,0.025,0.018,0.011],
    daily_profile: [0.165,0.163,0.162,0.165,0.158,0.098,0.089],
    los_median_h: 10.0, los_mean_h: 12.0, los_p90_h: 30.0, total_transfers: 10800,
  },

  // ── Clinical Decision Unit ───────────────────────────────────────────
  // Short observation, peaks 14-18:00
  "CDU": {
    hourly_profile: [0.015,0.012,0.010,0.008,0.007,0.007,0.008,0.012,0.020,0.032,0.042,0.052,0.058,0.065,0.072,0.078,0.080,0.075,0.068,0.060,0.052,0.045,0.035,0.022],
    daily_profile: [0.148,0.150,0.147,0.148,0.146,0.132,0.129],
    los_median_h: 8.0, los_mean_h: 10.0, los_p90_h: 24.0, total_transfers: 12400,
  },

  // ── Medicine ─────────────────────────────────────────────────────────
  // General medical ward, admissions peak late afternoon/evening
  "Medicine": {
    hourly_profile: [0.0457,0.0347,0.0265,0.0216,0.0171,0.0146,0.0121,0.0115,0.0139,0.012,0.0133,0.0168,0.0211,0.0278,0.0425,0.0541,0.073,0.0864,0.0925,0.0738,0.0826,0.0841,0.0718,0.0503],
    daily_profile: [0.1432,0.1424,0.1421,0.1433,0.1423,0.1425,0.1442],
    los_median_h: 120.0, los_mean_h: 144.0, los_p90_h: 288.0, total_transfers: 149824,
  },

  // ── Surgery ──────────────────────────────────────────────────────────
  // General surgical ward, admissions peak late afternoon
  "Surgery": {
    hourly_profile: [0.0409,0.0381,0.0297,0.0226,0.0183,0.0162,0.0131,0.0137,0.017,0.0147,0.0167,0.0216,0.0281,0.0343,0.0491,0.0546,0.0758,0.0842,0.0883,0.0646,0.0754,0.0724,0.065,0.0457],
    daily_profile: [0.1448,0.1385,0.1427,0.1432,0.1418,0.1437,0.1453],
    los_median_h: 96.0, los_mean_h: 108.0, los_p90_h: 240.0, total_transfers: 47492,
  },

  // ── Cardiology ───────────────────────────────────────────────────────
  "Cardiology": {
    hourly_profile: [0.0284,0.0222,0.0176,0.013,0.0115,0.0113,0.0098,0.0076,0.0101,0.0119,0.0176,0.0271,0.0356,0.0464,0.0627,0.0727,0.0943,0.1036,0.1088,0.0759,0.071,0.0627,0.0478,0.0301],
    daily_profile: [0.147,0.1414,0.1428,0.1406,0.1424,0.1422,0.1435],
    los_median_h: 72.0, los_mean_h: 96.0, los_p90_h: 192.0, total_transfers: 46417,
  },

  // ── Respiratory ──────────────────────────────────────────────────────
  // Similar admission profile to Medicine
  "Respiratory": {
    hourly_profile: [0.044,0.034,0.026,0.022,0.018,0.015,0.013,0.012,0.014,0.013,0.014,0.018,0.022,0.029,0.044,0.056,0.074,0.087,0.092,0.074,0.082,0.082,0.070,0.049],
    daily_profile: [0.145,0.144,0.143,0.144,0.143,0.141,0.140],
    los_median_h: 96.0, los_mean_h: 120.0, los_p90_h: 264.0, total_transfers: 32400,
  },

  // ── Orthopaedics ─────────────────────────────────────────────────────
  "Orthopaedics": {
    hourly_profile: [0.0409,0.0381,0.0297,0.0226,0.0183,0.0162,0.0131,0.0137,0.017,0.0147,0.0167,0.0216,0.0281,0.0343,0.0491,0.0546,0.0758,0.0842,0.0883,0.0646,0.0754,0.0724,0.065,0.0457],
    daily_profile: [0.145,0.139,0.143,0.143,0.142,0.144,0.145],
    los_median_h: 84.0, los_mean_h: 96.0, los_p90_h: 216.0, total_transfers: 23355,
  },

  // ── ICU ──────────────────────────────────────────────────────────────
  // Flat 24h profile, flat daily profile (emergency-driven)
  "ICU": {
    hourly_profile: [0.0427,0.0374,0.033,0.0268,0.0258,0.0245,0.0229,0.0192,0.0292,0.0472,0.0521,0.042,0.0368,0.0378,0.044,0.0414,0.0518,0.0586,0.064,0.0528,0.0569,0.0553,0.0527,0.0452],
    daily_profile: [0.1433,0.1448,0.1426,0.1434,0.1436,0.1412,0.141],
    los_median_h: 72.0, los_mean_h: 96.0, los_p90_h: 240.0, total_transfers: 92919,
  },

  // ── HDU (High Dependency Unit) ───────────────────────────────────────
  // Similar to ICU but shorter stay, relatively flat 24h
  "HDU": {
    hourly_profile: [0.042,0.038,0.034,0.028,0.026,0.025,0.024,0.020,0.030,0.046,0.050,0.044,0.040,0.040,0.044,0.042,0.050,0.056,0.060,0.052,0.054,0.052,0.048,0.042],
    daily_profile: [0.144,0.145,0.143,0.143,0.144,0.141,0.140],
    los_median_h: 48.0, los_mean_h: 60.0, los_p90_h: 144.0, total_transfers: 15600,
  },

  // ── Day Ward ─────────────────────────────────────────────────────────
  // Strongly morning-weighted (day cases arrive 08-12), weekday only
  "Day_Ward": {
    hourly_profile: [0.002,0.001,0.001,0.001,0.001,0.002,0.005,0.025,0.085,0.120,0.135,0.130,0.110,0.090,0.070,0.055,0.045,0.035,0.028,0.020,0.015,0.010,0.008,0.004],
    daily_profile: [0.195,0.198,0.195,0.196,0.190,0.016,0.010],
    los_median_h: 6.0, los_mean_h: 7.0, los_p90_h: 10.0, total_transfers: 22000,
  },

  // ── Discharge Lounge ─────────────────────────────────────────────────
  // Peaks 10-14:00 when discharges are processed, weekday-heavy
  "Discharge_Lounge": {
    hourly_profile: [0.005,0.003,0.002,0.002,0.002,0.003,0.005,0.012,0.035,0.065,0.095,0.110,0.115,0.105,0.090,0.075,0.060,0.050,0.042,0.035,0.028,0.022,0.015,0.008],
    daily_profile: [0.175,0.178,0.175,0.176,0.170,0.068,0.058],
    los_median_h: 2.0, los_mean_h: 3.0, los_p90_h: 6.0, total_transfers: 19500,
  },
};

/**
 * Get the arrival intensity multiplier for a department at a given sim time.
 * Returns a value where 1.0 = average, >1 = busier than average, <1 = quieter.
 */
export function getArrivalIntensity(deptName: string, simTimeSecs: number): number {
  const profile = mimicArrivals[deptName];
  if (!profile) return 1.0;

  const totalHours = simTimeSecs / 3600;
  const hourOfDay = Math.floor(totalHours % 24);
  const dayOfWeek = Math.floor((totalHours / 24) % 7);

  // Hourly factor: normalized to mean=1.0
  const hourlyMean = 1 / 24; // uniform baseline
  const hourlyFactor = profile.hourly_profile[hourOfDay] / hourlyMean;

  // Daily factor: normalized to mean=1.0
  const dailyMean = 1 / 7;
  const dailyFactor = profile.daily_profile[dayOfWeek] / dailyMean;

  return hourlyFactor * dailyFactor;
}
