import { useState, useEffect } from "react";
import { NavLink, Outlet, useLocation, useMatch } from "react-router-dom";
import {
  LayoutDashboard,
  Stethoscope,
  HeartPulse,
  Building2,
  Microscope,
  GitBranch,
  Radio,
  MessageCircle,
  ChevronLeft,
  ChevronRight,
  Activity,
  Shield,
  Sun,
  Moon,
  BedDouble,
  ClipboardList,
  FileText,
  Workflow,
  Database,
  DoorOpen,
  Brain,
  Link2,
  AlertTriangle,
  Search,
  Menu,
  X,
  Route,
} from "lucide-react";
import AlertCenter from "./AlertCenter";

const navItems = [
  { path: "/", label: "Dashboard", icon: LayoutDashboard },
  { path: "/ed-triage", label: "ED Triage", icon: Stethoscope },
  { path: "/ed-flow", label: "ED Flow", icon: Workflow },
  { path: "/sepsis", label: "Sepsis & ICU", icon: HeartPulse },
  { path: "/hospital-ops", label: "Hospital Ops", icon: Building2 },
  { path: "/bed-management", label: "Bed Management", icon: BedDouble },
  { path: "/oncology", label: "Oncology AI", icon: Microscope },
  { path: "/waiting-list", label: "Waiting List", icon: ClipboardList },
  { path: "/patient-journey", label: "Patient Journey", icon: GitBranch },
  { path: "/voyage", label: "Patient Voyage", icon: Route },
  { path: "/clinical-scribe", label: "Clinical Scribe", icon: FileText },
  { path: "/simulation", label: "Simulation", icon: Radio },
  { path: "/chat", label: "Clinical Chat", icon: MessageCircle },
  { path: "/erp", label: "Hospital ERP", icon: Database },
  { path: "---", label: "divider", icon: LayoutDashboard },
  // Uplift: 6 new services (8216–8221)
  { path: "/deterioration", label: "Deterioration (NEWS2)", icon: AlertTriangle },
  { path: "/discharge-lounge", label: "Discharge Lounge", icon: DoorOpen },
  { path: "/trolley", label: "Trolley Watch", icon: Activity },
  { path: "/fhir", label: "FHIR Gateway", icon: Link2 },
  { path: "/xai", label: "XAI Audit", icon: Brain },
  { path: "/gdpr", label: "GDPR Compliance", icon: Shield },
  { path: "---", label: "divider", icon: LayoutDashboard },
  { path: "/system", label: "System Admin", icon: Shield },
];

const pageTitles: Record<string, string> = {
  "/": "Platform Overview",
  "/ed-triage": "ED Triage AI",
  "/ed-flow": "ED Flow Optimizer",
  "/sepsis": "Sepsis & ICU Watch",
  "/hospital-ops": "Hospital Operations",
  "/bed-management": "Bed Management & Discharge Prediction",
  "/oncology": "Oncology AI",
  "/waiting-list": "Waiting List Intelligence",
  "/patient-journey": "Patient Journey",
  "/voyage": "Patient Voyage — Live Visualization",
  "/clinical-scribe": "AI Clinical Scribe",
  "/simulation": "Data Ingestion / Simulation",
  "/chat": "Clinical Chat",
  "/erp": "Hospital ERP — Master Data",
  "/deterioration": "Predictive Deterioration Monitor (NEWS2)",
  "/discharge-lounge": "Discharge Lounge Coordinator",
  "/trolley": "HSE Trolley Watch",
  "/fhir": "National EHR Gateway (FHIR R4)",
  "/xai": "Explainability & Audit (EU AI Act)",
  "/gdpr": "GDPR Compliance Engine",
  "/system": "System Admin",
};

function ThemeToggle({ collapsed }: { collapsed: boolean }) {
  const [dark, setDark] = useState(() => {
    if (typeof window !== "undefined") {
      return localStorage.getItem("theme") === "dark";
    }
    return false;
  });

  useEffect(() => {
    if (dark) {
      document.documentElement.classList.add("dark");
      localStorage.setItem("theme", "dark");
    } else {
      document.documentElement.classList.remove("dark");
      localStorage.setItem("theme", "light");
    }
  }, [dark]);

  // Dark mode currently only re-themes the chrome (sidebar, header,
  // top-level cards). Most chart panels and data widgets keep their
  // light-theme tokens — labelling the toggle "(beta)" so users know
  // what to expect rather than promising a full dark theme. (F29)
  return (
    <button
      type="button"
      onClick={() => setDark(!dark)}
      className="flex items-center gap-3 mx-2 px-3 py-2.5 rounded-lg transition-colors text-slate-400 hover:text-slate-200 hover:bg-slate-700/50"
      title={dark ? "Switch to light mode" : "Switch to dark mode (chrome only — body panels still light)"}
      aria-label={dark ? "Switch to light mode" : "Switch to dark mode (beta)"}
    >
      {dark ? (
        <Sun className="w-5 h-5 shrink-0 text-amber-400" aria-hidden="true" />
      ) : (
        <Moon className="w-5 h-5 shrink-0 text-slate-500" aria-hidden="true" />
      )}
      {!collapsed && (
        <span className="text-sm font-medium whitespace-nowrap">
          {dark ? "Light Mode" : "Dark Mode"}
          <span className="ml-1 text-[10px] uppercase tracking-wide text-slate-500">beta</span>
        </span>
      )}
    </button>
  );
}

export default function Layout() {
  const [collapsed, setCollapsed] = useState(false);
  // Mobile drawer (visible <lg). Hidden by default — opens via the
  // hamburger button in the header. Body scroll is locked while open.
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [currentTime, setCurrentTime] = useState(new Date());
  const location = useLocation();
  // /patient/:id is dynamic; resolve a friendly title without falling
  // back to the generic "Dashboard" string.
  const patientMatch = useMatch("/patient/:id");

  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  // Close the mobile drawer whenever the user navigates to a new route.
  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.pathname]);

  // Lock background scroll while the drawer is open.
  useEffect(() => {
    if (mobileNavOpen) {
      const prev = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = prev;
      };
    }
  }, [mobileNavOpen]);

  const pageTitle = patientMatch
    ? `Patient #${patientMatch.params.id}`
    : pageTitles[location.pathname] || "Dashboard";

  const renderNavItems = () =>
    navItems.map((item, idx) => {
      if (item.path === "---") {
        return (
          <div
            key={`divider-${idx}`}
            role="separator"
            className="my-2 mx-2 border-t border-border"
          />
        );
      }
      return (
        <NavLink
          key={item.path}
          to={item.path}
          end={item.path === "/"}
          className={({ isActive }) =>
            `flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors group relative ${
              isActive
                ? "bg-blue-500/10 text-blue-400"
                : "text-slate-400 hover:text-slate-200 hover:bg-slate-700/50"
            }`
          }
        >
          {({ isActive }) => (
            <>
              {isActive && (
                <div
                  aria-hidden="true"
                  className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-6 bg-blue-400 rounded-r"
                />
              )}
              <item.icon className="w-5 h-5 shrink-0" aria-hidden="true" />
              {!collapsed && (
                <span className="text-sm font-medium whitespace-nowrap">
                  {item.label}
                </span>
              )}
            </>
          )}
        </NavLink>
      );
    });

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Skip link for keyboard / screen reader users */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:px-3 focus:py-2 focus:rounded-lg focus:bg-blue-600 focus:text-white focus:shadow-lg"
      >
        Skip to main content
      </a>

      {/* Mobile drawer backdrop */}
      {mobileNavOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          className="lg:hidden fixed inset-0 z-30 bg-black/60 backdrop-blur-sm"
          onClick={() => setMobileNavOpen(false)}
        />
      )}

      {/* Sidebar — collapses to a slide-out drawer below lg (1024 px) */}
      <aside
        aria-label="Primary navigation"
        className={`fixed lg:static inset-y-0 left-0 z-40 flex flex-col bg-bg-card border-r border-border transition-all duration-300 ${
          collapsed ? "lg:w-16" : "lg:w-56"
        } ${
          mobileNavOpen
            ? "w-64 translate-x-0"
            : "w-64 -translate-x-full lg:translate-x-0"
        }`}
      >
        {/* Logo */}
        <div className="flex items-center gap-3 px-4 h-14 border-b border-border">
          <Activity
            className="w-7 h-7 text-blue-400 shrink-0"
            aria-hidden="true"
          />
          {!collapsed && (
            <span className="text-base font-bold text-white whitespace-nowrap">
              MedAI Platform
            </span>
          )}
          {/* Close drawer (mobile only) */}
          <button
            type="button"
            aria-label="Close navigation"
            onClick={() => setMobileNavOpen(false)}
            className="lg:hidden ml-auto text-slate-400 hover:text-slate-200"
          >
            <X className="w-5 h-5" aria-hidden="true" />
          </button>
        </div>

        {/* Navigation — internal scroll with subtle gradient cue so the
            user can tell there's more content when items overflow. */}
        <div className="relative flex-1 min-h-0">
          <nav
            aria-label="Main"
            className="absolute inset-0 py-3 space-y-1 px-2 overflow-y-auto"
          >
            {renderNavItems()}
          </nav>
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 bottom-0 h-6 bg-gradient-to-t from-bg-card to-transparent"
          />
        </div>

        {/* Theme Toggle */}
        <div className="border-t border-border py-2">
          <ThemeToggle collapsed={collapsed} />
        </div>

        {/* Collapse toggle (desktop only — mobile uses drawer X) */}
        <button
          type="button"
          onClick={() => setCollapsed(!collapsed)}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!collapsed}
          className="hidden lg:flex items-center justify-center py-3 border-t border-border text-slate-500 hover:text-slate-300 transition-colors"
        >
          {collapsed ? (
            <ChevronRight className="w-4 h-4" aria-hidden="true" />
          ) : (
            <ChevronLeft className="w-4 h-4" aria-hidden="true" />
          )}
        </button>

        {/* Branding */}
        <div className="px-4 py-3 border-t border-border">
          {!collapsed ? (
            <p className="text-[11px] text-slate-500 text-center uppercase tracking-wider">
              Harishankar Somasundaram
            </p>
          ) : (
            <p className="text-[11px] text-slate-500 text-center">HS</p>
          )}
        </div>
      </aside>

      {/* Main Area */}
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        {/* Top Header */}
        <header className="flex items-center justify-between gap-3 px-4 lg:px-6 h-14 bg-bg-card border-b border-border shrink-0">
          {/* Hamburger (mobile only) */}
          <button
            type="button"
            aria-label="Open navigation"
            aria-expanded={mobileNavOpen}
            aria-controls="primary-navigation"
            onClick={() => setMobileNavOpen(true)}
            className="lg:hidden flex items-center justify-center w-9 h-9 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-700/50 transition-colors"
          >
            <Menu className="w-5 h-5" aria-hidden="true" />
          </button>
          <h1 className="text-base sm:text-lg font-semibold text-white truncate flex-1 min-w-0">
            {pageTitle}
          </h1>
          <div className="flex items-center gap-2 sm:gap-3 shrink-0">
            <button
              type="button"
              onClick={() => {
                const ev = new KeyboardEvent("keydown", {
                  key: "k",
                  metaKey: true,
                  ctrlKey: true,
                  bubbles: true,
                });
                window.dispatchEvent(ev);
              }}
              className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-slate-700/40 hover:bg-slate-700/70 text-slate-400 hover:text-slate-200 text-xs transition-colors"
              title="Search (⌘K / Ctrl+K)"
              aria-label="Open search (Cmd K)"
            >
              <Search className="w-3.5 h-3.5" aria-hidden="true" />
              <span className="hidden sm:inline">Search...</span>
              <kbd className="hidden md:inline text-[11px] px-1 py-0.5 rounded border border-border font-mono-clinical">
                ⌘K
              </kbd>
            </button>
            <AlertCenter />
            <div className="hidden sm:flex items-center gap-2 text-sm text-slate-400">
              <div
                className="w-2 h-2 rounded-full bg-green-500 animate-pulse"
                aria-hidden="true"
              />
              <span>System Online</span>
            </div>
            <div className="hidden md:block font-mono-clinical text-sm text-slate-300">
              {currentTime.toLocaleDateString("en-US", {
                weekday: "short",
                month: "short",
                day: "numeric",
              })}{" "}
              {currentTime.toLocaleTimeString("en-US", {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
                hour12: false,
              })}
            </div>
          </div>
        </header>

        {/* Page Content */}
        <main
          id="main-content"
          className="flex-1 overflow-auto bg-bg-primary p-3 sm:p-5"
        >
          <div className="animate-fade-in">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
