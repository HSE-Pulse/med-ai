interface TimelineEvent {
  date: string;
  title: string;
  category: string;
  description: string;
  priority?: string;
  estimatedDays?: number;
}

interface TimelineViewProps {
  events: TimelineEvent[];
}

const categoryConfig: Record<string, { color: string; bg: string; label: string }> = {
  surgery: { color: "#DC2626", bg: "rgba(220,38,38,0.15)", label: "Surgery" },
  chemo: { color: "#F97316", bg: "rgba(249,115,22,0.15)", label: "Chemotherapy" },
  chemotherapy: { color: "#F97316", bg: "rgba(249,115,22,0.15)", label: "Chemotherapy" },
  radiation: { color: "#A855F7", bg: "rgba(168,85,247,0.15)", label: "Radiation" },
  diagnostic: { color: "#3B82F6", bg: "rgba(59,130,246,0.15)", label: "Diagnostic" },
  followup: { color: "#22C55E", bg: "rgba(34,197,94,0.15)", label: "Follow-up" },
  supportive: { color: "#22C55E", bg: "rgba(34,197,94,0.15)", label: "Supportive" },
};

const defaultCategory = { color: "#64748B", bg: "rgba(100,116,139,0.15)", label: "Other" };

export default function TimelineView({ events }: TimelineViewProps) {
  return (
    <div className="relative">
      {/* Vertical line */}
      <div className="absolute left-5 top-0 bottom-0 w-px bg-border" />

      <div className="space-y-4">
        {events.map((event, idx) => {
          const config = categoryConfig[event.category] || defaultCategory;
          return (
            <div key={idx} className="relative flex gap-4 pl-2">
              {/* Step circle */}
              <div
                className="relative z-10 flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold shrink-0 border-2"
                style={{
                  borderColor: config.color,
                  backgroundColor: config.bg,
                  color: config.color,
                }}
              >
                {idx + 1}
              </div>

              {/* Content */}
              <div className="flex-1 bg-bg-card rounded-lg border border-border p-3 hover:border-slate-600 transition-colors">
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-white">
                      {event.title}
                    </span>
                    <span
                      className="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
                      style={{
                        color: config.color,
                        backgroundColor: config.bg,
                      }}
                    >
                      {config.label}
                    </span>
                    {event.priority && (
                      <span
                        className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
                          event.priority === "high" || event.priority === "immediate"
                            ? "text-red-400 bg-red-400/10"
                            : event.priority === "medium" || event.priority === "scheduled"
                            ? "text-yellow-400 bg-yellow-400/10"
                            : "text-green-400 bg-green-400/10"
                        }`}
                      >
                        {event.priority}
                      </span>
                    )}
                  </div>
                  <span className="text-[10px] text-slate-500">{event.date}</span>
                </div>
                <p className="text-xs text-slate-400">{event.description}</p>
                {event.estimatedDays !== undefined && (
                  <p className="text-[10px] text-slate-500 mt-1">
                    Duration: ~{event.estimatedDays} days
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
