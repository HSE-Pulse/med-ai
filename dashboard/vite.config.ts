import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Each /api/* prefix → backend service. Defaults to the host-based
// loopback ports so a developer can run `npm run dev` against
// start_all.py. In Docker, override per service via VITE_*_URL env vars
// (set in docker-compose.services.yml) so the dashboard talks to the
// sibling container by service-name DNS instead.
function backendTargets(env: Record<string, string>) {
  const t = (envKey: string, port: number) =>
    env[envKey] ?? `http://127.0.0.1:${port}`;
  return {
    "/api/erp": t("VITE_ERP_URL", 8215),
    "/api/ed-flow": t("VITE_ED_FLOW_URL", 8214),
    "/api/ed": t("VITE_ED_TRIAGE_URL", 8201),
    "/api/sepsis": t("VITE_SEPSIS_ICU_URL", 8202),
    "/api/ops": t("VITE_HOSPITAL_OPS_URL", 8203),
    "/api/onco": t("VITE_ONCOLOGY_AI_URL", 8204),
    "/api/journey": t("VITE_PATIENT_JOURNEY_URL", 8205),
    "/api/chat": t("VITE_CLINICAL_CHAT_URL", 8206),
    "/api/sim": t("VITE_DATA_INGESTION_URL", 8207),
    "/api/beds": t("VITE_BED_MANAGEMENT_URL", 8208),
    "/api/waitlist": t("VITE_WAITING_LIST_URL", 8209),
    "/api/scribe": t("VITE_CLINICAL_SCRIBE_URL", 8210),
    "/api/trolley": t("VITE_TROLLEY_WATCH_URL", 8216),
    "/api/gdpr": t("VITE_GDPR_URL", 8217),
    "/api/xai": t("VITE_XAI_URL", 8218),
    "/api/fhir": t("VITE_FHIR_URL", 8219),
    "/api/deterioration": t("VITE_DETERIORATION_URL", 8220),
    "/api/discharge-lounge": t("VITE_DISCHARGE_LOUNGE_URL", 8221),
    "/api/alerts": t("VITE_ALERTS_URL", 8222),
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  // Vite ignores process.env unless prefixed with VITE_; loadEnv reads
  // .env files. To pick up plain env vars passed by docker-compose,
  // merge them in here.
  const merged = { ...env, ...process.env } as Record<string, string>;
  const targets = backendTargets(merged);

  const rewriteFor = (prefix: string) => (path: string) =>
    path.replace(new RegExp(`^${prefix}`), "");

  const proxy: Record<string, {
    target: string;
    changeOrigin: boolean;
    rewrite: (p: string) => string;
    ws?: boolean;
  }> = {};
  for (const [prefix, target] of Object.entries(targets)) {
    proxy[prefix] = {
      target,
      changeOrigin: true,
      rewrite: rewriteFor(prefix),
      // /api/sim and /api/alerts proxy WebSocket traffic too.
      ws: prefix === "/api/sim" || prefix === "/api/alerts",
    };
  }

  return {
    plugins: [react(), tailwindcss()],
    server: {
      host: "0.0.0.0",
      port: 3010,
      allowedHosts: ["homelab", "localhost", "127.0.0.1"],
      proxy,
    },
  };
});
