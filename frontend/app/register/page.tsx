"use client";

import Link from "next/link";
import { useState } from "react";

import { STRINGS, UI_LANGUAGES, type UiLang } from "@/lib/i18n";

// The one page reachable without an account: redeem an invite, create a login. It attaches no
// token (see app/api/register/route.ts) — the invite code is the gate. On success it does NOT
// log the user in; it points them at signing in, which keeps account creation and holding a
// session two separate, deliberate steps.
export default function RegisterPage() {
  const [lang, setLang] = useState<UiLang>("en");
  const [code, setCode] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  const t = STRINGS[lang];
  const r = t.register;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, email, password, name: name.trim() || null }),
      });
      if (!res.ok) {
        const data = await res.json();
        const kind = data.kind as keyof typeof r.errors;
        setError(r.errors[kind] ?? r.errors.server);
      } else {
        setDone(true);
      }
    } catch {
      setError(r.errors.server);
    } finally {
      setLoading(false);
    }
  }

  const field =
    "w-full rounded-md border border-neutral-300 p-2.5 text-sm outline-none focus:border-neutral-500 dark:border-neutral-700 dark:bg-neutral-900";

  return (
    <main className="min-h-screen px-6 py-12">
      <div className="mx-auto max-w-md">
        <div className="flex items-baseline justify-between">
          <h1 className="text-xl font-semibold tracking-tight">LuxMedicine</h1>
          <select
            value={lang}
            onChange={(e) => setLang(e.target.value as UiLang)}
            className="rounded-md border border-neutral-300 px-2 py-1 text-xs dark:border-neutral-700 dark:bg-neutral-900"
          >
            {UI_LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </div>

        {done ? (
          <section className="mt-8 rounded-lg border border-green-300 bg-green-50 p-4 text-sm text-green-900 dark:border-green-900 dark:bg-green-950 dark:text-green-100">
            <p>{r.success}</p>
            <Link href="/" className="mt-3 inline-block font-medium underline">
              LuxMedicine →
            </Link>
          </section>
        ) : (
          <>
            <h2 className="mt-8 text-lg font-medium">{r.heading}</h2>
            <p className="mt-1 text-sm text-neutral-500">{r.intro}</p>

            <form onSubmit={submit} className="mt-6 space-y-4">
              <div>
                <label className="mb-1 block text-sm font-medium">{r.code}</label>
                <input
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  required
                  autoComplete="off"
                  className={field}
                />
                <p className="mt-1 text-xs text-neutral-500">{r.codeHint}</p>
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium">{r.email}</label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoComplete="email"
                  className={field}
                />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium">{r.password}</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="new-password"
                  className={field}
                />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium">{r.name}</label>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoComplete="name"
                  className={field}
                />
              </div>

              {error && (
                <p
                  role="alert"
                  className="rounded-md border border-red-300 bg-red-50 p-2.5 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200"
                >
                  {error}
                </p>
              )}

              <button
                type="submit"
                disabled={loading || !code.trim() || !email.trim() || !password}
                className="w-full rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40 dark:bg-white dark:text-neutral-900"
              >
                {loading ? r.submitting : r.submit}
              </button>
            </form>
          </>
        )}
      </div>
    </main>
  );
}
