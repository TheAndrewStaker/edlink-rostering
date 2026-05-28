import { NavLink, Outlet } from "react-router-dom";

import { ConnectorDialogOutlets } from "@/components/ConnectorActions";
import { DevPersonaSwitcher } from "@/components/DevPersonaSwitcher";
import { LeaDetailDialogOutlets } from "@/components/LeaDetailPanel";
import { useIsAuthenticated } from "@/lib/useAuth";

interface NavItem {
  to: string;
  label: string;
  end?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/leas", label: "LEAs" },
  { to: "/integrations", label: "Integrations" },
  { to: "/admin/audit", label: "Audit" },
];

export function App() {
  const isAuthenticated = useIsAuthenticated();

  if (!isAuthenticated) {
    return <SignInScreen />;
  }

  return (
    <div style={{ minHeight: "100vh", background: "var(--bg)" }}>
      <div className="chrome-top">
        <div className="chrome-brand">
          <span className="logo">
            <span className="glyph">E</span>
            EdLink Rostering
          </span>
          <span className="crumb">integration &middot; admin</span>
        </div>
        <nav className="chrome-nav">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => (isActive ? "on" : "")}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="chrome-right">
          <DevPersonaSwitcher />
        </div>
      </div>

      <div className="page-body">
        <Outlet />
      </div>

      <LeaDetailDialogOutlets />
      <ConnectorDialogOutlets />
    </div>
  );
}

function SignInScreen() {
  return (
    <div
      style={{
        minHeight: "100vh",
        background: "var(--bg)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: "var(--font-body)",
      }}
    >
      <div
        style={{
          background: "var(--panel)",
          border: "1px solid var(--rule)",
          borderRadius: 12,
          padding: "40px 48px",
          textAlign: "center",
          maxWidth: 400,
          width: "100%",
          boxShadow: "0 4px 16px rgba(0,0,0,0.06)",
        }}
      >
        <div style={{ marginBottom: 24 }}>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 36,
              height: 36,
              borderRadius: 8,
              background: "var(--ink)",
              color: "#fff",
              fontFamily: "var(--font-mono)",
              fontSize: 18,
              fontWeight: 700,
              marginBottom: 16,
            }}
          >
            E
          </span>
          <h1
            style={{
              fontSize: 20,
              fontWeight: 600,
              color: "var(--ink)",
              margin: "0 0 6px 0",
              letterSpacing: "-0.01em",
            }}
          >
            EdLink Rostering Admin
          </h1>
          <p
            style={{
              fontSize: 13,
              color: "var(--ink-3)",
              margin: 0,
            }}
          >
            Select a persona to sign in to the control plane.
          </p>
        </div>
        <DevPersonaSwitcher />
      </div>
    </div>
  );
}
