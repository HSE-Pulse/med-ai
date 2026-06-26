interface AcuityBadgeProps {
  level: 1 | 2 | 3 | 4 | 5;
  size?: "sm" | "md" | "lg";
}

const acuityConfig = {
  1: { label: "Resuscitation", color: "#DC2626", bg: "rgba(220,38,38,0.15)" },
  2: { label: "Emergent", color: "#F97316", bg: "rgba(249,115,22,0.15)" },
  3: { label: "Urgent", color: "#EAB308", bg: "rgba(234,179,8,0.15)" },
  4: { label: "Less Urgent", color: "#22C55E", bg: "rgba(34,197,94,0.15)" },
  5: { label: "Non-Urgent", color: "#3B82F6", bg: "rgba(59,130,246,0.15)" },
};

const sizeClasses = {
  sm: "text-[10px] px-1.5 py-0.5",
  md: "text-xs px-2 py-1",
  lg: "text-sm px-3 py-1.5",
};

export default function AcuityBadge({ level, size = "md" }: AcuityBadgeProps) {
  const config = acuityConfig[level];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full font-semibold whitespace-nowrap ${sizeClasses[size]}`}
      style={{
        color: config.color,
        backgroundColor: config.bg,
        border: `1px solid ${config.color}30`,
      }}
    >
      <span className="font-mono-clinical">ESI-{level}</span>
      {size !== "sm" && <span className="hidden sm:inline">{config.label}</span>}
    </span>
  );
}
