/**
 * Reusable skeleton loading placeholder.
 *
 * Extracted from PatientJourney.tsx for consistent loading states
 * across all dashboard pages.
 */
export default function SkeletonBlock({ className = "" }: { className?: string }) {
  return <div className={`skeleton rounded ${className}`} />;
}
