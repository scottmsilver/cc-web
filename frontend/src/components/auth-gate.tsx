"use client";

import { useEffect, useState } from "react";

import { CCHOST_API } from "@/lib/config";

type State =
  | { kind: "checking" }
  | { kind: "signed-in"; email: string }
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<State>({ kind: "checking" });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch(`${CCHOST_API}/api/auth/me`, { credentials: "include" });
        if (cancelled) return;
        if (res.status === 401) {
          setState({ kind: "signed-out" });
          return;
        }
        if (!res.ok) {
          const text = await res.text().catch(() => "");
          setState({ kind: "error", message: `HTTP ${res.status} ${text}` });
          return;
        }
        const data = (await res.json()) as { email: string };
        setState({ kind: "signed-in", email: data.email });
      } catch (err) {
        if (cancelled) return;
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : "auth check failed",
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.kind === "checking") {
    return (
      <div className="flex h-screen items-center justify-center bg-th-bg text-th-text-muted">
        Checking sign-in…
      </div>
    );
  }

  if (state.kind === "signed-out" || state.kind === "error") {
    const here = typeof window !== "undefined" ? window.location.pathname + window.location.search : "/";
    const loginUrl = `${CCHOST_API}/api/auth/login?return_url=${encodeURIComponent(here)}`;
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-6 bg-th-bg text-th-text">
        <h1 className="text-2xl font-medium">cchost</h1>
        {state.kind === "error" ? (
          <p className="text-sm text-red-500">Auth check failed: {state.message}</p>
        ) : null}
        <a
          href={loginUrl}
          className="rounded-lg bg-th-accent px-6 py-2.5 text-sm font-medium text-white transition-opacity hover:opacity-90"
        >
          Sign in with Google
        </a>
      </div>
    );
  }

  return <>{children}</>;
}
