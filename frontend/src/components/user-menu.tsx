"use client";

import { useEffect, useState } from "react";

import { CCHOST_API } from "@/lib/config";

type Me = { email: string; picture?: string | null; name?: string | null };

export function UserMenu() {
  const [me, setMe] = useState<Me | null>(null);
  const [open, setOpen] = useState(false);
  const [pictureBroken, setPictureBroken] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch(`${CCHOST_API}/api/auth/me`, { credentials: "include" });
        if (cancelled || !res.ok) return;
        const data = (await res.json()) as Me;
        setMe(data);
      } catch {
        /* ignore — AuthGate already handles unauthenticated state */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!me) return null;
  const email = me.email;

  const signOut = async () => {
    await fetch(`${CCHOST_API}/api/auth/logout`, {
      method: "POST",
      credentials: "include",
    });
    window.location.href = "/";
  };

  const switchAccount = async () => {
    await fetch(`${CCHOST_API}/api/auth/logout`, {
      method: "POST",
      credentials: "include",
    });
    const here = window.location.pathname + window.location.search;
    window.location.href = `${CCHOST_API}/api/auth/login?return_url=${encodeURIComponent(here)}`;
  };

  const initial = (email[0] || "?").toUpperCase();
  // Deterministic-ish hue from the email so different users get distinct avatar colors.
  let hash = 0;
  for (let i = 0; i < email.length; i++) hash = (hash * 31 + email.charCodeAt(i)) >>> 0;
  const hue = hash % 360;
  const showPicture = Boolean(me.picture) && !pictureBroken;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex h-8 w-8 items-center justify-center overflow-hidden rounded-full text-xs font-semibold text-white transition-opacity hover:opacity-85 cursor-pointer"
        style={showPicture ? undefined : { backgroundColor: `hsl(${hue} 55% 45%)` }}
        title={email}
        aria-label={`Signed in as ${email}`}
      >
        {showPicture ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={me.picture as string}
            alt=""
            className="h-full w-full object-cover"
            referrerPolicy="no-referrer"
            onError={() => setPictureBroken(true)}
          />
        ) : (
          initial
        )}
      </button>
      {open && (
        <>
          <div
            className="fixed inset-0 z-30"
            onClick={() => setOpen(false)}
          />
          <div className="absolute right-0 top-full z-40 mt-1 w-56 rounded-lg border border-th-border bg-th-bg shadow-lg p-1">
            <div className="px-2.5 py-2 text-[11px] text-th-text-muted" title={email}>
              {me.name ? (
                <>
                  <span className="block text-th-text font-medium truncate">{me.name}</span>
                  <span className="block truncate">{email}</span>
                </>
              ) : (
                <>
                  Signed in as<br />
                  <span className="text-th-text font-medium truncate block">{email}</span>
                </>
              )}
            </div>
            <div className="my-1 border-t border-th-border" />
            <button
              onClick={() => void switchAccount()}
              className="w-full text-left px-2.5 py-1.5 rounded text-xs text-th-text hover:bg-th-surface cursor-pointer"
            >
              Switch account
            </button>
            <button
              onClick={() => void signOut()}
              className="w-full text-left px-2.5 py-1.5 rounded text-xs text-th-text hover:bg-th-surface cursor-pointer"
            >
              Sign out
            </button>
          </div>
        </>
      )}
    </div>
  );
}
