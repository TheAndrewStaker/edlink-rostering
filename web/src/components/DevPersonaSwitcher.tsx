import { Menu, Portal } from "@chakra-ui/react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { clearJwt, setJwt } from "@/api/client";
import { labelForRole } from "@/lib/labels";
import { notifyError, notifySuccess } from "@/lib/notify";
import { notifyAuthChange } from "@/lib/useAuth";

interface Persona {
  subject: string;
  email: string;
  name: string;
  role: "owner" | "admin" | "operator" | "auditor";
  scopeDescription: string;
  initials: string;
}

const PERSONAS: Persona[] = [
  {
    subject: "stephen-dev-001",
    email: "stephen@edlink-rostering.test",
    name: "Stephen Staker",
    role: "owner",
    scopeDescription: "all LEAs",
    initials: "SS",
  },
  {
    subject: "admin-dev-001",
    email: "admin@edlink-rostering.test",
    name: "Admin User",
    role: "owner",
    scopeDescription: "all LEAs",
    initials: "AU",
  },
  {
    subject: "qa-dev-001",
    email: "qa@edlink-rostering.test",
    name: "QA Dev",
    role: "admin",
    scopeDescription: "all LEAs",
    initials: "QD",
  },
  {
    subject: "lakewood-ops-001",
    email: "lakewood@edlink-rostering.test",
    name: "Lakewood Ops",
    role: "operator",
    scopeDescription: "lea-lakewood-usd only",
    initials: "LO",
  },
  {
    subject: "district-ops-001",
    email: "ops@edlink-rostering.test",
    name: "District Ops",
    role: "operator",
    scopeDescription: "lea-riverside-usd only",
    initials: "DO",
  },
  {
    subject: "auditor-001",
    email: "auditor@edlink-rostering.test",
    name: "Read-only Auditor",
    role: "auditor",
    scopeDescription: "read-only across all",
    initials: "RA",
  },
];

const ACTIVE_SUBJECT_KEY = "edlink.persona_subject";

export function DevPersonaSwitcher() {
  const qc = useQueryClient();
  const [active, setActive] = useState<Persona | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const subject = localStorage.getItem(ACTIVE_SUBJECT_KEY);
    if (!subject) return;
    const match = PERSONAS.find((p) => p.subject === subject);
    if (match) setActive(match);
  }, []);

  if (!import.meta.env.DEV) {
    return null;
  }

  function signOut() {
    clearJwt();
    localStorage.removeItem(ACTIVE_SUBJECT_KEY);
    setActive(null);
    notifyAuthChange();
    qc.clear();
  }

  async function switchTo(persona: Persona) {
    setBusy(true);
    try {
      const response = await fetch("/api/v1/dev/mint-jwt", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          subject: persona.subject,
          email: persona.email,
          name: persona.name,
          expires_in_minutes: 60,
        }),
      });
      if (!response.ok) {
        throw new Error(`mint-jwt returned ${response.status}`);
      }
      const body = (await response.json()) as { token: string };
      setJwt(body.token);
      localStorage.setItem(ACTIVE_SUBJECT_KEY, persona.subject);
      setActive(persona);
      notifyAuthChange();
      qc.invalidateQueries();
      notifySuccess(
        `Signed in as ${persona.name}`,
        `${labelForRole(persona.role)} · ${persona.scopeDescription}`,
      );
    } catch (err) {
      notifyError(
        "Persona switch failed",
        err instanceof Error ? err.message : String(err),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Menu.Root>
      <Menu.Trigger asChild>
        <button
          className="persona"
          disabled={busy}
          type="button"
          style={{ opacity: busy ? 0.6 : 1 }}
        >
          <span className="avatar">{active?.initials ?? "?"}</span>
          {active ? active.name : "Sign in as..."}
          {active && (
            <span className="role">{labelForRole(active.role)}</span>
          )}
        </button>
      </Menu.Trigger>
      <Portal>
        <Menu.Positioner>
          <Menu.Content
            style={{
              background: "var(--panel)",
              border: "1px solid var(--rule-strong)",
              borderRadius: "8px",
              boxShadow: "0 8px 28px rgba(0,0,0,0.18)",
              padding: "4px",
              minWidth: "280px",
            }}
          >
            {PERSONAS.map((persona) => (
              <Menu.Item
                key={persona.subject}
                value={persona.subject}
                onClick={() => void switchTo(persona)}
                style={{
                  padding: "8px 12px",
                  borderRadius: "6px",
                  cursor: "pointer",
                  fontSize: "13px",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 10, width: "100%" }}>
                  <span
                    className="avatar"
                    style={{
                      width: 20,
                      height: 20,
                      borderRadius: "50%",
                      background: "var(--mute-fill)",
                      color: "var(--mute-ink)",
                      fontFamily: "var(--font-mono)",
                      fontSize: 10,
                      fontWeight: 700,
                      display: "inline-flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    {persona.initials}
                  </span>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 500, fontSize: 13 }}>{persona.name}</div>
                    <div
                      style={{
                        fontSize: 11,
                        color: "var(--ink-3)",
                        fontFamily: "var(--font-mono)",
                      }}
                    >
                      {labelForRole(persona.role)} &middot; {persona.scopeDescription}
                    </div>
                  </div>
                </div>
              </Menu.Item>
            ))}
            {active && (
              <>
                <Menu.Separator
                  style={{
                    borderTop: "1px solid var(--rule)",
                    margin: "4px 2px",
                  }}
                />
                <Menu.Item
                  value="sign-out"
                  onClick={signOut}
                  style={{
                    padding: "8px 12px",
                    borderRadius: "6px",
                    cursor: "pointer",
                    fontSize: "13px",
                    color: "var(--ink-3)",
                  }}
                >
                  Sign out
                </Menu.Item>
              </>
            )}
          </Menu.Content>
        </Menu.Positioner>
      </Portal>
    </Menu.Root>
  );
}
