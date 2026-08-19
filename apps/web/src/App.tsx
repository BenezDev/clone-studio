import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import { useAsync } from "./components/ui";
import { api } from "./lib/api";
import Assets from "./pages/Assets";
import Dashboard from "./pages/Dashboard";
import Diagnostics from "./pages/Diagnostics";
import Identity from "./pages/Identity";
import Models from "./pages/Models";
import NewVideo from "./pages/NewVideo";
import RenderQueue from "./pages/RenderQueue";
import ScriptPage from "./pages/Script";
import SettingsPage from "./pages/Settings";
import Templates from "./pages/Templates";
import Voice from "./pages/Voice";

const NAV = [
  {
    label: "Produção",
    items: [
      { to: "/dashboard", icon: "▦", label: "Dashboard" },
      { to: "/new", icon: "✦", label: "New Video" },
      { to: "/queue", icon: "≡", label: "Render Queue" },
    ],
  },
  {
    label: "Identidade",
    items: [
      { to: "/voice", icon: "◍", label: "Voice" },
      { to: "/templates", icon: "▤", label: "Templates" },
      { to: "/identity", icon: "◈", label: "Identity" },
      { to: "/assets", icon: "◫", label: "Assets" },
    ],
  },
  {
    label: "Sistema",
    items: [
      { to: "/models", icon: "◉", label: "Models" },
      { to: "/settings", icon: "⚙", label: "Settings" },
      { to: "/diagnostics", icon: "◎", label: "Diagnostics" },
    ],
  },
];

function Sidebar() {
  const { data: health } = useAsync(() => api.health(), []);
  const { data: jobs } = useAsync(() => api.jobs.list(), []);

  const active = jobs?.jobs.filter(
    (j) => !["completed", "failed", "cancelled"].includes(j.state),
  ).length;

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-name">Local Clone Studio</div>
        <div className="brand-sub">
          {health ? `${health.hardware_profile} · local-first` : "conectando…"}
        </div>
      </div>

      <nav className="nav">
        {NAV.map((group) => (
          <div className="nav-group" key={group.label}>
            <div className="nav-group-label">{group.label}</div>
            {group.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `nav-item ${isActive ? "active" : ""}`
                }
              >
                <span className="nav-icon">{item.icon}</span>
                <span>{item.label}</span>
                {item.to === "/queue" && !!active && (
                  <span className="nav-badge">{active}</span>
                )}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      <div className="sidebar-footer">
        <div>Sem telemetria · sem nuvem</div>
        <div style={{ marginTop: 2 }}>127.0.0.1 apenas</div>
      </div>
    </aside>
  );
}

export default function App() {
  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/new" element={<NewVideo />} />
          <Route path="/new/:projectId" element={<NewVideo />} />
          <Route path="/script/:projectId" element={<ScriptPage />} />
          <Route path="/queue" element={<RenderQueue />} />
          <Route path="/voice" element={<Voice />} />
          <Route path="/templates" element={<Templates />} />
          <Route path="/identity" element={<Identity />} />
          <Route path="/assets" element={<Assets />} />
          <Route path="/models" element={<Models />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/diagnostics" element={<Diagnostics />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </main>
    </div>
  );
}
