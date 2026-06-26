export function formatDate(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso.replace(" ", "T")).toLocaleString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function formatTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso.replace(" ", "T")).toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function computeLosHours(admittime?: string, dischtime?: string): number | null {
  if (!admittime || !dischtime) return null;
  const a = Date.parse(admittime.replace(" ", "T"));
  const d = Date.parse(dischtime.replace(" ", "T"));
  if (!Number.isFinite(a) || !Number.isFinite(d)) return null;
  return Math.max(0, (d - a) / 3600000);
}

export function formatLos(hours: number | null): string {
  if (hours === null) return "—";
  return hours < 24 ? `${hours.toFixed(1)} h` : `${(hours / 24).toFixed(1)} d`;
}
