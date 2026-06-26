/**
 * Shared Recharts styling constants.
 *
 * Consolidates the repeated CartesianGrid, Tooltip, and Axis tick
 * styling used across BedManagement, EDFlowOptimizer, WaitingList, etc.
 */

export const CHART_GRID_PROPS = {
  strokeDasharray: "3 3",
  stroke: "var(--color-chart-grid)",
  opacity: 0.3,
} as const;

export const CHART_TOOLTIP_STYLE = {
  backgroundColor: "var(--color-tooltip-bg)",
  border: "1px solid var(--color-tooltip-border)",
  borderRadius: 8,
} as const;

export const AXIS_TICK_STYLE = {
  fontSize: 10,
  fill: "#94a3b8",
} as const;

export const AXIS_TICK_SMALL = {
  fontSize: 9,
  fill: "#64748b",
} as const;
