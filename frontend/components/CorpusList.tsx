"use client";

import { useEffect, useState } from "react";

import type { DocumentSummary } from "@/lib/api";
import type { Sector, UiLang } from "@/lib/i18n";

// Local strings (admin/sidebar chrome), like CorpusUpload — kept out of the typed STRINGS to avoid
// widening it across three languages; de/ka machine-drafted, native review pending.
type ListStrings = {
  heading: string;
  empty: string;
  error: string;
  loading: string;
  count: (n: number) => string;
};

// en/de/ka; the additional languages fall back to English via `?? .en` below.
const STRINGS: Partial<Record<UiLang, ListStrings>> & { en: ListStrings } = {
  en: {
    heading: "Guidelines in this corpus",
    empty: "No documents in this corpus yet.",
    error: "Could not load the list.",
    loading: "Loading…",
    count: (n) => `${n} document${n === 1 ? "" : "s"}`,
  },
  de: {
    heading: "Leitlinien in diesem Korpus",
    empty: "Noch keine Dokumente in diesem Korpus.",
    error: "Liste konnte nicht geladen werden.",
    loading: "Wird geladen…",
    count: (n) => `${n} Dokument${n === 1 ? "" : "e"}`,
  },
  ka: {
    heading: "ამ კორპუსის სახელმძღვანელოები",
    empty: "ამ კორპუსში ჯერ დოკუმენტი არ არის.",
    error: "სია ვერ ჩაიტვირთა.",
    loading: "იტვირთება…",
    count: (n) => `${n} დოკუმენტი`,
  },
  ru: { heading: "Руководства в этом корпусе", empty: "В этом корпусе пока нет документов.", error: "Не удалось загрузить список.", loading: "Загрузка…", count: (n) => `${n} док.` },
  uk: { heading: "Настанови в цьому корпусі", empty: "У цьому корпусі поки немає документів.", error: "Не вдалося завантажити список.", loading: "Завантаження…", count: (n) => `${n} док.` },
  fr: { heading: "Recommandations de ce corpus", empty: "Aucun document dans ce corpus pour l'instant.", error: "Impossible de charger la liste.", loading: "Chargement…", count: (n) => `${n} document${n === 1 ? "" : "s"}` },
  es: { heading: "Guías de este corpus", empty: "Aún no hay documentos en este corpus.", error: "No se pudo cargar la lista.", loading: "Cargando…", count: (n) => `${n} documento${n === 1 ? "" : "s"}` },
  it: { heading: "Linee guida in questo corpus", empty: "Nessun documento in questo corpus.", error: "Impossibile caricare l'elenco.", loading: "Caricamento…", count: (n) => `${n} document${n === 1 ? "o" : "i"}` },
  pl: { heading: "Wytyczne w tym korpusie", empty: "Brak dokumentów w tym korpusie.", error: "Nie udało się załadować listy.", loading: "Ładowanie…", count: (n) => `${n} dok.` },
  tr: { heading: "Bu külliyattaki kılavuzlar", empty: "Bu külliyatta henüz belge yok.", error: "Liste yüklenemedi.", loading: "Yükleniyor…", count: (n) => `${n} belge` },
  ar: { heading: "الإرشادات في هذه المجموعة", empty: "لا توجد مستندات في هذه المجموعة بعد.", error: "تعذّر تحميل القائمة.", loading: "جارٍ التحميل…", count: (n) => `${n} مستند` },
  pt: { heading: "Diretrizes neste corpus", empty: "Ainda não há documentos neste corpus.", error: "Não foi possível carregar a lista.", loading: "A carregar…", count: (n) => `${n} documento${n === 1 ? "" : "s"}` },
  nl: { heading: "Richtlijnen in dit corpus", empty: "Nog geen documenten in dit corpus.", error: "Kon de lijst niet laden.", loading: "Laden…", count: (n) => `${n} document${n === 1 ? "" : "en"}` },
  ro: { heading: "Ghiduri în acest corpus", empty: "Încă nu există documente în acest corpus.", error: "Lista nu a putut fi încărcată.", loading: "Se încarcă…", count: (n) => `${n} document${n === 1 ? "" : "e"}` },
  el: { heading: "Οδηγίες σε αυτό το σώμα κειμένων", empty: "Δεν υπάρχουν ακόμη έγγραφα εδώ.", error: "Δεν ήταν δυνατή η φόρτωση της λίστας.", loading: "Φόρτωση…", count: (n) => `${n} έγγραφ${n === 1 ? "ο" : "α"}` },
  cs: { heading: "Doporučení v tomto korpusu", empty: "V tomto korpusu zatím nejsou žádné dokumenty.", error: "Seznam se nepodařilo načíst.", loading: "Načítání…", count: (n) => `${n} dok.` },
  hu: { heading: "Irányelvek ebben a korpuszban", empty: "Még nincsenek dokumentumok ebben a korpuszban.", error: "A lista nem tölthető be.", loading: "Betöltés…", count: (n) => `${n} dokumentum` },
};

export function CorpusList({ sector, lang }: { sector: Sector; lang: UiLang }) {
  const t = STRINGS[lang] ?? STRINGS.en;
  // One state object stamped with the sector it belongs to, set only from the async result — so
  // there is no synchronous setState in the effect (react-hooks/set-state-in-effect), and a stale
  // response for a sector we have since switched away from is ignored.
  type Loaded = { sector: string; docs: DocumentSummary[] } | { sector: string; error: true };
  const [loaded, setLoaded] = useState<Loaded | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/documents?sector=${sector}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => {
        if (!cancelled) setLoaded({ sector, docs: d.documents as DocumentSummary[] });
      })
      .catch(() => {
        if (!cancelled) setLoaded({ sector, error: true });
      });
    return () => {
      cancelled = true;
    };
  }, [sector]);

  // Trust the data only if it is for the currently selected sector; otherwise we are still loading.
  const current = loaded && loaded.sector === sector ? loaded : null;
  const docs = current && "docs" in current ? current.docs : null;
  const error = Boolean(current && "error" in current);
  const loading = current === null;

  // Group by issuing organisation, in first-seen order (the list is already sorted by org on the backend).
  const groups: { org: string; items: DocumentSummary[] }[] = [];
  for (const d of docs ?? []) {
    const g = groups.find((x) => x.org === d.issuing_org);
    if (g) g.items.push(d);
    else groups.push({ org: d.issuing_org, items: [d] });
  }

  return (
    <aside className="hidden w-72 shrink-0 overflow-y-auto border-r border-neutral-200 p-4 md:block dark:border-neutral-800">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-400">{t.heading}</h2>
      {docs && <p className="mt-1 text-[11px] text-neutral-400">{t.count(docs.length)}</p>}
      {loading && <p className="mt-3 text-xs text-neutral-400">{t.loading}</p>}
      {error && <p className="mt-3 text-xs text-red-500">{t.error}</p>}
      {docs && docs.length === 0 && <p className="mt-3 text-xs text-neutral-400">{t.empty}</p>}

      <div className="mt-3 space-y-4">
        {groups.map((g) => (
          <div key={g.org}>
            <p className="text-[11px] font-medium text-neutral-500">{g.org}</p>
            <ul className="mt-1 space-y-1">
              {g.items.map((d, i) => {
                const inactive = d.status !== "active";
                return (
                  <li
                    key={i}
                    className={`text-xs leading-snug ${inactive ? "text-neutral-400 dark:text-neutral-500" : "text-neutral-700 dark:text-neutral-300"}`}
                  >
                    {d.title}
                    {d.version_label && <span className="text-neutral-400"> · {d.version_label}</span>}
                    {inactive && (
                      <span className="ml-1 rounded bg-neutral-200 px-1 py-0.5 text-[10px] text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400">
                        {d.status}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </aside>
  );
}
