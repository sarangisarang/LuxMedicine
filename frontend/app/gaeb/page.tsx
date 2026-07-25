"use client";

import Link from "next/link";
import { useRef, useState } from "react";

import type { GaebBoQ, GaebPosition } from "@/lib/api";
import { UI_LANGUAGES, type UiLang } from "@/lib/i18n";

// A German construction tool that lives behind the same login. Its copy is kept here, not in the
// shared STRINGS — en/de/ka with an English fallback (de is the primary user, this being Baurecht).
type GaebStrings = {
  title: string;
  intro: string;
  choose: string;
  noFile: string;
  convert: string;
  converting: string;
  verifyNote: string;
  colOz: string;
  colText: string;
  colQty: string;
  colUnit: string;
  colPrice: string;
  colSection: string;
  colTotal: string;
  colFileTotal: string;
  mismatchWarning: (n: number) => string;
  addRow: string;
  removeRow: string;
  estimated: string;
  export: string;
  exporting: string;
  needFile: string;
  back: string;
};

const STRINGS: Partial<Record<UiLang, GaebStrings>> & { en: GaebStrings } = {
  en: {
    title: "GAEB converter",
    intro:
      "Turn a bill of quantities (Leistungsverzeichnis) — Excel, CSV, Word, PDF, or an existing GAEB file — into a GAEB DA XML .x84 offer. Check the positions and prices, then export. (PDF extraction is best-effort; verify it closely.)",
    choose: "Choose file",
    noFile: "No file chosen",
    convert: "Read positions",
    converting: "Reading…",
    verifyNote:
      "Verify every position and unit price before you export — a wrong price in a bid is real money. Nothing is exported that you have not confirmed.",
    colOz: "OZ",
    colText: "Description",
    colQty: "Qty",
    colUnit: "Unit",
    colPrice: "Unit price",
    colSection: "Section",
    colTotal: "Total",
    colFileTotal: "In the file",
    mismatchWarning: (n) =>
      `${n} row${n === 1 ? "" : "s"} do not match the total printed in the source file. A column was probably read wrongly — check the quantity and unit price on the rows marked in red before exporting.`,
    addRow: "+ Add position",
    removeRow: "Remove",
    estimated: "Estimated total",
    export: "Export .x84",
    exporting: "Generating…",
    needFile: "Choose an LV file (Excel, CSV, or GAEB) first.",
    back: "← Back",
  },
  de: {
    title: "GAEB-Konverter",
    intro:
      "Ein Leistungsverzeichnis — Excel, CSV, Word, PDF oder eine vorhandene GAEB-Datei — in ein GAEB-DA-XML-.x84-Angebot umwandeln. Positionen und Preise prüfen, dann exportieren. (PDF-Extraktion ist ein Näherungswert; genau prüfen.)",
    choose: "Datei wählen",
    noFile: "Keine Datei gewählt",
    convert: "Positionen einlesen",
    converting: "Wird eingelesen…",
    verifyNote:
      "Prüfen Sie jede Position und jeden Einheitspreis vor dem Export — ein falscher Preis im Angebot ist echtes Geld. Es wird nichts exportiert, was Sie nicht bestätigt haben.",
    colOz: "OZ",
    colText: "Bezeichnung",
    colQty: "Menge",
    colUnit: "Einheit",
    colPrice: "Einheitspreis",
    colSection: "Titel",
    colTotal: "Gesamt",
    colFileTotal: "In der Datei",
    mismatchWarning: (n) =>
      `${n} Position${n === 1 ? "" : "en"} stimmen nicht mit dem in der Datei ausgewiesenen Gesamtbetrag überein. Vermutlich wurde eine Spalte falsch gelesen — prüfen Sie Menge und Einheitspreis der rot markierten Zeilen vor dem Export.`,
    addRow: "+ Position hinzufügen",
    removeRow: "Entfernen",
    estimated: "Geschätzte Summe",
    export: ".x84 exportieren",
    exporting: "Wird erzeugt…",
    needFile: "Wählen Sie zuerst eine LV-Datei (Excel, CSV oder GAEB).",
    back: "← Zurück",
  },
  ka: {
    title: "GAEB-კონვერტერი",
    intro:
      "სამუშაოთა ნუსხა (LV) — Excel, CSV, Word, PDF ან არსებული GAEB ფაილი — გადააქციე GAEB DA XML .x84 შეთავაზებად. შეამოწმე პოზიციები და ფასები, მერე ექსპორტი. (PDF-ის ამოღება მიახლოებითია — კარგად შეამოწმე.)",
    choose: "ფაილის არჩევა",
    noFile: "ფაილი არ არის არჩეული",
    convert: "პოზიციების წაკითხვა",
    converting: "იკითხება…",
    verifyNote:
      "ექსპორტამდე შეამოწმე ყოველი პოზიცია და ერთეულის ფასი — არასწორი ფასი ბიდში რეალური ფულია. არაფერი ექსპორტდება, რაც არ დაგიდასტურებია.",
    colOz: "OZ",
    colText: "აღწერა",
    colQty: "რაოდ.",
    colUnit: "ერთ.",
    colPrice: "ერთ. ფასი",
    colSection: "სექცია",
    colTotal: "ჯამი",
    colFileTotal: "ფაილში",
    mismatchWarning: (n) =>
      `${n} მწკრივი არ ემთხვევა ფაილში მითითებულ ჯამს. სავარაუდოდ სვეტი არასწორად წაიკითხა — ექსპორტამდე შეამოწმე წითლად მონიშნული მწკრივების რაოდენობა და ერთეულის ფასი.`,
    addRow: "+ პოზიციის დამატება",
    removeRow: "წაშლა",
    estimated: "სავარაუდო ჯამი",
    export: ".x84 ექსპორტი",
    exporting: "იქმნება…",
    needFile: "ჯერ აირჩიე LV ფაილი (Excel, CSV ან GAEB).",
    back: "← უკან",
  },
};

const EMPTY: GaebPosition = {
  oz: "",
  short_text: "",
  quantity: "",
  unit: "",
  unit_price: "",
  long_text: null,
  section: "",
};

function toNumber(raw: string | null): number {
  if (!raw) return 0;
  // Lenient parse for the on-screen estimate only; the backend re-parses authoritatively.
  const s = raw.replace(/\s|€|EUR/g, "");
  const normalised =
    s.includes(",") && s.lastIndexOf(",") > s.lastIndexOf(".")
      ? s.replace(/\./g, "").replace(",", ".")
      : s.replace(/,/g, "");
  const n = Number(normalised);
  return Number.isFinite(n) ? n : 0;
}

export default function GaebPage() {
  const [lang, setLang] = useState<UiLang>("de");
  const t = STRINGS[lang] ?? STRINGS.en;

  const inputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState("");
  const [projectName, setProjectName] = useState("");
  const [positions, setPositions] = useState<GaebPosition[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function update(i: number, field: keyof GaebPosition, value: string) {
    setPositions((prev) =>
      prev ? prev.map((p, j) => (j === i ? { ...p, [field]: value } : p)) : prev,
    );
  }

  function removeRow(i: number) {
    setPositions((prev) => (prev ? prev.filter((_, j) => j !== i) : prev));
  }

  function addRow() {
    setPositions((prev) => [...(prev ?? []), { ...EMPTY }]);
  }

  async function convert() {
    setError(null);
    const file = inputRef.current?.files?.[0];
    if (!file) {
      setError(t.needFile);
      return;
    }
    // The project is simply what the file is called — no separate field to fill in. The name drives
    // the .x84's PrjInfo and the download filename, so "Freianlagen.pdf" comes back "Freianlagen.x84".
    const base = file.name.replace(/\.[^.]+$/, "");
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("project_name", base);

    setBusy(true);
    try {
      const res = await fetch("/api/gaeb/parse", { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? `HTTP ${res.status}`);
        setPositions(null);
        return;
      }
      const boq = data as GaebBoQ;
      setProjectName(base);
      setPositions(boq.positions);
    } catch {
      setError("The server could not be reached. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  }

  async function exportX84() {
    if (!positions) return;
    setError(null);
    setBusy(true);
    try {
      const res = await fetch("/api/gaeb/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_name: projectName, currency: "EUR", positions }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
        setError(data.error ?? `HTTP ${res.status}`);
        return;
      }
      const blob = await res.blob();
      const disposition = res.headers.get("content-disposition") ?? "";
      const match = disposition.match(/filename="?([^"]+)"?/);
      const name = match ? match[1] : "angebot.x84";
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      setError("The server could not be reached. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  }

  // A row disagrees when our Qty × UP does not match the total the source file printed for it.
  function mismatched(p: GaebPosition): boolean {
    if (!p.source_total) return false;
    return Math.abs(toNumber(p.quantity) * toNumber(p.unit_price) - toNumber(p.source_total)) > 0.02;
  }
  const mismatches = (positions ?? []).filter(mismatched).length;

  const total = (positions ?? []).reduce(
    (sum, p) => sum + toNumber(p.quantity) * toNumber(p.unit_price),
    0,
  );

  return (
    <main className="mx-auto max-w-5xl px-6 py-8">
      <div className="flex items-center justify-between">
        <Link href="/" className="text-xs text-neutral-500 underline">
          {t.back}
        </Link>
        <select
          value={lang}
          onChange={(e) => setLang(e.target.value as UiLang)}
          className="rounded-md border border-neutral-300 px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-900"
        >
          {UI_LANGUAGES.map((l) => (
            <option key={l.code} value={l.code}>
              {l.label}
            </option>
          ))}
        </select>
      </div>

      <h1 className="mt-4 text-2xl font-semibold">{t.title}</h1>
      <p className="mt-2 max-w-2xl text-sm text-neutral-500">{t.intro}</p>

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <label className="cursor-pointer rounded-md border border-neutral-300 bg-neutral-100 px-3 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-800">
          {t.choose}
          <input
            ref={inputRef}
            type="file"
            accept=".xlsx,.xlsm,.csv,.txt,.docx,.pdf,.x81,.x82,.x83,.x84,.x85,.x86,.xml"
            onChange={(e) => setFileName(e.target.files?.[0]?.name ?? "")}
            className="sr-only"
          />
        </label>
        <span className="text-xs text-neutral-500">{fileName || t.noFile}</span>
        <button
          type="button"
          onClick={convert}
          disabled={busy}
          className="rounded-md bg-neutral-900 px-4 py-1.5 text-sm text-white disabled:opacity-40 dark:bg-white dark:text-neutral-900"
        >
          {busy && !positions ? t.converting : t.convert}
        </button>
      </div>

      {error && <p className="mt-4 text-sm text-red-600 dark:text-red-400">{error}</p>}

      {positions && (
        <div className="mt-6">
          <p className="mb-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-300">
            {t.verifyNote}
          </p>

          {mismatches > 0 && (
            <p className="mb-3 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950 dark:text-red-300">
              {t.mismatchWarning(mismatches)}
            </p>
          )}

          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-neutral-200 text-left text-xs text-neutral-500 dark:border-neutral-800">
                  <th className="py-2 pr-2">{t.colOz}</th>
                  <th className="py-2 pr-2">{t.colText}</th>
                  <th className="py-2 pr-2">{t.colQty}</th>
                  <th className="py-2 pr-2">{t.colUnit}</th>
                  <th className="py-2 pr-2">{t.colPrice}</th>
                  <th className="py-2 pr-2">{t.colSection}</th>
                  <th className="py-2 pr-2 text-right">{t.colTotal}</th>
                  <th className="py-2 pr-2 text-right">{t.colFileTotal}</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => (
                  <tr key={i} className="border-b border-neutral-100 dark:border-neutral-900">
                    <td className="py-1 pr-2">
                      <input value={p.oz} onChange={(e) => update(i, "oz", e.target.value)} className="w-20 rounded border border-neutral-200 bg-transparent px-1 py-0.5 dark:border-neutral-800" />
                    </td>
                    <td className="py-1 pr-2">
                      <input value={p.short_text} onChange={(e) => update(i, "short_text", e.target.value)} className="w-full min-w-[14rem] rounded border border-neutral-200 bg-transparent px-1 py-0.5 dark:border-neutral-800" />
                      {/* The Langtext — the wrapped lines carrying the DIN references and the
                          technical qualifiers. Shown, not hidden: a bidder has to read them, and
                          they travel into the .x84 as the position's detail text. */}
                      {p.long_text && (
                        <textarea
                          value={p.long_text}
                          onChange={(e) => update(i, "long_text", e.target.value)}
                          rows={Math.min(6, p.long_text.split("\n").length + 1)}
                          className="mt-1 w-full min-w-[14rem] rounded border border-neutral-200 bg-transparent px-1 py-0.5 text-xs text-neutral-500 dark:border-neutral-800"
                        />
                      )}
                    </td>
                    <td className="py-1 pr-2">
                      <input value={p.quantity} onChange={(e) => update(i, "quantity", e.target.value)} className="w-16 rounded border border-neutral-200 bg-transparent px-1 py-0.5 text-right dark:border-neutral-800" />
                    </td>
                    <td className="py-1 pr-2">
                      <input value={p.unit} onChange={(e) => update(i, "unit", e.target.value)} className="w-14 rounded border border-neutral-200 bg-transparent px-1 py-0.5 dark:border-neutral-800" />
                    </td>
                    <td className="py-1 pr-2">
                      <input value={p.unit_price ?? ""} onChange={(e) => update(i, "unit_price", e.target.value)} className="w-20 rounded border border-neutral-200 bg-transparent px-1 py-0.5 text-right dark:border-neutral-800" />
                    </td>
                    <td className="py-1 pr-2">
                      <input value={p.section} onChange={(e) => update(i, "section", e.target.value)} className="w-28 rounded border border-neutral-200 bg-transparent px-1 py-0.5 dark:border-neutral-800" />
                    </td>
                    <td className={`py-1 pr-2 text-right tabular-nums ${mismatched(p) ? "font-semibold text-red-600 dark:text-red-400" : "text-neutral-500"}`}>
                      {(toNumber(p.quantity) * toNumber(p.unit_price)).toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    {/* The file's own line total, side by side with ours. If the two differ, a
                        column was read wrongly — better seen than silently exported. */}
                    <td className={`py-1 pr-2 text-right tabular-nums ${mismatched(p) ? "text-red-600 dark:text-red-400" : "text-neutral-400"}`}>
                      {p.source_total
                        ? toNumber(p.source_total).toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                        : "—"}
                    </td>
                    <td className="py-1">
                      <button type="button" onClick={() => removeRow(i)} className="text-xs text-neutral-400 hover:text-red-500">
                        {t.removeRow}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <button type="button" onClick={addRow} className="text-sm text-neutral-500 underline">
              {t.addRow}
            </button>
            <p className="text-sm text-neutral-600 dark:text-neutral-300">
              {t.estimated}:{" "}
              <span className="font-semibold tabular-nums">
                {total.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
              </span>
            </p>
          </div>

          <button
            type="button"
            onClick={exportX84}
            disabled={busy}
            className="mt-4 rounded-md bg-emerald-600 px-5 py-2 text-sm font-medium text-white disabled:opacity-40"
          >
            {busy ? t.exporting : t.export}
          </button>
        </div>
      )}
    </main>
  );
}
