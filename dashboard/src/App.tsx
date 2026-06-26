import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import { AlertsProvider } from "./context/AlertsContext";
import CommandPalette from "./components/CommandPalette";
import PatientUnified from "./pages/PatientUnified";
import Overview from "./pages/Overview";
import EdTriage from "./pages/EdTriage";
import SepsisIcu from "./pages/SepsisIcu";
import HospitalOps from "./pages/HospitalOps";
import Oncology from "./pages/Oncology";
import PatientJourney from "./pages/PatientJourney";
import SystemAdmin from "./pages/SystemAdmin";
import ClinicalChat from "./pages/ClinicalChat";
import SimulationControl from "./pages/SimulationControl";
import BedManagement from "./pages/BedManagement";
import WaitingList from "./pages/WaitingList";
import ClinicalScribe from "./pages/ClinicalScribe";
import EDFlowOptimizer from "./pages/EDFlowOptimizer";
import HospitalERP from "./pages/HospitalERP";
import TrolleyWatch from "./pages/TrolleyWatch";
import GDPRCompliance from "./pages/GDPRCompliance";
import XAIAudit from "./pages/XAIAudit";
import FHIRGateway from "./pages/FHIRGateway";
import DeteriorationMonitor from "./pages/DeteriorationMonitor";
import DischargeLounge from "./pages/DischargeLounge";
import PatientVoyage from "./pages/PatientVoyage";

export default function App() {
  return (
    <AlertsProvider>
      <CommandPalette />
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Overview />} />
          <Route path="/patient/:id" element={<PatientUnified />} />
          <Route path="/ed-triage" element={<EdTriage />} />
        <Route path="/ed-flow" element={<EDFlowOptimizer />} />
        <Route path="/sepsis" element={<SepsisIcu />} />
        <Route path="/hospital-ops" element={<HospitalOps />} />
        <Route path="/bed-management" element={<BedManagement />} />
        <Route path="/oncology" element={<Oncology />} />
        <Route path="/waiting-list" element={<WaitingList />} />
        <Route path="/patient-journey" element={<PatientJourney />} />
        <Route path="/voyage" element={<PatientVoyage />} />
        <Route path="/clinical-scribe" element={<ClinicalScribe />} />
        <Route path="/simulation" element={<SimulationControl />} />
        <Route path="/chat" element={<ClinicalChat />} />
        <Route path="/erp" element={<HospitalERP />} />
        <Route path="/deterioration" element={<DeteriorationMonitor />} />
        <Route path="/discharge-lounge" element={<DischargeLounge />} />
        <Route path="/trolley" element={<TrolleyWatch />} />
        <Route path="/fhir" element={<FHIRGateway />} />
        <Route path="/xai" element={<XAIAudit />} />
        <Route path="/gdpr" element={<GDPRCompliance />} />
        <Route path="/system" element={<SystemAdmin />} />
      </Route>
    </Routes>
    </AlertsProvider>
  );
}
