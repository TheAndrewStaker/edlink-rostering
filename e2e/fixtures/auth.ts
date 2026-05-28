/**
 * Auth helpers for Playwright specs.
 *
 * Mints HS256 JWTs via the dev-only `/api/v1/dev/mint-jwt` endpoint and
 * writes the token into the page's localStorage under
 * `edlink.jwt`, matching the persona switcher's storage key. Specs
 * call `signIn(page, "admin")` and the page loads
 * pre-authenticated.
 *
 * Persona subjects map to the seeded operators in
 * `edlink_rostering/dev/seed.py`. Adding a new role here requires a
 * matching seeded operator with the right grants.
 */

import type { APIRequestContext, Page } from "@playwright/test";
import { request } from "@playwright/test";

export type Role = "owner" | "admin" | "operator" | "auditor";

interface Persona {
  subject: string;
  email: string;
  name: string;
}

const PERSONAS: Record<Role, Persona> = {
  owner: {
    subject: "stephen-dev-001",
    email: "stephen@edlink.test",
    name: "Stephen Staker",
  },
  admin: {
    subject: "qa-dev-001",
    email: "qa@edlink.test",
    name: "QA Dev",
  },
  operator: {
    subject: "lakewood-ops-001",
    email: "lakewood@edlink.test",
    name: "Lakewood Ops",
  },
  auditor: {
    subject: "auditor-001",
    email: "auditor@edlink.test",
    name: "Read-only Auditor",
  },
};

const API_PORT = Number(process.env.PORT_API ?? 8100);
export const API_BASE_URL = `http://127.0.0.1:${API_PORT}`;

export async function mintJwt(
  apiRequest: APIRequestContext,
  role: Role,
): Promise<string> {
  const persona = PERSONAS[role];
  const response = await apiRequest.post(`${API_BASE_URL}/api/v1/dev/mint-jwt`, {
    data: {
      subject: persona.subject,
      email: persona.email,
      name: persona.name,
      expires_in_minutes: 60,
    },
  });
  if (!response.ok()) {
    throw new Error(
      `mint-jwt failed for ${role}: ${response.status()} ${await response.text()}`,
    );
  }
  const body = (await response.json()) as { token: string };
  return body.token;
}

export async function signIn(page: Page, role: Role): Promise<void> {
  const apiRequest = await request.newContext();
  try {
    const token = await mintJwt(apiRequest, role);
    const persona = PERSONAS[role];
    await page.goto("/");
    await page.evaluate(
      ({ token, subject }) => {
        window.localStorage.setItem("edlink.jwt", token);
        window.localStorage.setItem("edlink.persona_subject", subject);
      },
      { token, subject: persona.subject },
    );
  } finally {
    await apiRequest.dispose();
  }
}
