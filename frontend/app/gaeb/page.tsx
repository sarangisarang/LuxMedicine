"use client";

import Link from "next/link";
import { useRef, useState } from "react";

import type { GaebBoQ, GaebEntry } from "@/lib/api";
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
  colKgOz: string;
  mapperTitle: string;
  mapperHint: string;
  roleIgnore: string;
  roleText: string;
  dataStartsAt: string;
  colText: string;
  colQty: string;
  colUnit: string;
  colPrice: string;
  colTeilbetrag: string;
  colTotal: string;
  mismatchWarning: (n: number) => string;
  addRow: string;
  removeRow: string;
  noSumNote: string;
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
    colKgOz: "KG / OZ",
    mapperTitle: "Source columns — correct them if a column was read wrongly",
    mapperHint: "These are the file's own columns as extracted. Say what each one is; the table below follows immediately.",
    roleIgnore: "— ignore —",
    roleText: "Description / Quelleinträge",
    dataStartsAt: "Data starts at row",
    colText: "DIN 276 (2018-12) / Quelleinträge",
    colQty: "Menge/Einheit",
    colUnit: "Unit",
    colPrice: "Unit price",
    colTeilbetrag: "Teilbetrag / EP",
    colTotal: "Total EUR",
    mismatchWarning: (n) =>
      `${n} row${n === 1 ? "" : "s"} do not match the total printed in the source file. A column was probably read wrongly — check the quantity and unit price on the rows marked in red before exporting.`,
    addRow: "+ Add position",
    removeRow: "Remove",
    noSumNote: "All values are taken from the source file unchanged. Nothing is calculated, summed or adjusted here.",
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
    colKgOz: "KG / OZ",
    mapperTitle: "Spalten der Quelldatei — bei falscher Zuordnung hier korrigieren",
    mapperHint: "Das sind die Spalten der Datei, wie sie eingelesen wurden. Geben Sie an, was jede Spalte ist; die Tabelle darunter folgt sofort.",
    roleIgnore: "— ignorieren —",
    roleText: "Bezeichnung / Quelleinträge",
    dataStartsAt: "Daten beginnen in Zeile",
    colText: "DIN 276 (2018-12) / Quelleinträge",
    colQty: "Menge/Einheit",
    colUnit: "Einheit",
    colPrice: "Einheitspreis",
    colTeilbetrag: "Teilbetrag / EP",
    colTotal: "Gesamt EUR",
    mismatchWarning: (n) =>
      `${n} Position${n === 1 ? "" : "en"} stimmen nicht mit dem in der Datei ausgewiesenen Gesamtbetrag überein. Vermutlich wurde eine Spalte falsch gelesen — prüfen Sie Menge und Einheitspreis der rot markierten Zeilen vor dem Export.`,
    addRow: "+ Position hinzufügen",
    removeRow: "Entfernen",
    noSumNote: "Sämtliche Werte werden unverändert aus der Quelldatei übernommen. Es werden hier keine Berechnungen, Summierungen oder Mengenanpassungen durchgeführt.",
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
    colKgOz: "KG / OZ",
    mapperTitle: "წყაროს სვეტები — თუ არასწორად წაიკითხა, აქ გაასწორე",
    mapperHint: "ეს არის ფაილის სვეტები ისე, როგორც წაიკითხა. მიუთითე, რომელი რაა — ქვემოთ ცხრილი მაშინვე განახლდება.",
    roleIgnore: "— არ გამოიყენო —",
    roleText: "დასახელება / Quelleinträge",
    dataStartsAt: "მონაცემები იწყება მწკრივიდან",
    colText: "DIN 276 (2018-12) / Quelleinträge",
    colQty: "Menge/Einheit",
    colUnit: "ერთ.",
    colPrice: "ერთ. ფასი",
    colTeilbetrag: "Teilbetrag / EP",
    colTotal: "ჯამი EUR",
    mismatchWarning: (n) =>
      `${n} მწკრივი არ ემთხვევა ფაილში მითითებულ ჯამს. სავარაუდოდ სვეტი არასწორად წაიკითხა — ექსპორტამდე შეამოწმე წითლად მონიშნული მწკრივების რაოდენობა და ერთეულის ფასი.`,
    addRow: "+ პოზიციის დამატება",
    removeRow: "წაშლა",
    noSumNote: "ყველა მნიშვნელობა უცვლელად არის აღებული წყარო-ფაილიდან. აქ არაფერი ითვლება, ჯამდება ან სწორდება.",
    export: ".x84 ექსპორტი",
    exporting: "იქმნება…",
    needFile: "ჯერ აირჩიე LV ფაილი (Excel, CSV ან GAEB).",
    back: "← უკან",
  },
};

const EMPTY: GaebEntry = {
  kind: "position",
  number: "",
  text: "",
  menge_einheit: "",
  teilbetrag_ep: "",
  gesamt: "",
  level: 0,
  kg: [],
  long_text: null,
};

// Which of the source's columns is which. "—" means the column carries nothing we need.
const ROLES = ["oz", "short_text", "quantity", "teilbetrag", "total", ""] as const;
type Role = (typeof ROLES)[number];

// A DIN 276 cost group number: 500 is the first level, 520 the second, 522 the third, 522.1 a
// fourth. Mirrors kg_level() on the server, so the table groups rows the way the reader does.
function kgLevel(value: string): number {
  const m = /^([1-8])(\d)(\d)(?:[.\-/](\d+))?$/.exec((value || "").trim());
  if (!m) return 0;
  if (m[4]) return 4;
  if (m[3] !== "0") return 3;
  if (m[2] !== "0") return 2;
  return 1;
}

/** Build the displayed rows from the raw grid and the current column assignment. Nothing is
 *  computed here — each cell is carried across exactly as the file wrote it. */
function rowsFromGrid(grid: string[][], roles: Role[], startAt: number): GaebEntry[] {
  const at = (row: string[], role: Role): string => {
    const i = roles.indexOf(role);
    return i >= 0 && i < row.length ? (row[i] ?? "").trim() : "";
  };
  const out: GaebEntry[] = [];
  grid.slice(startAt).forEach((row) => {
    if (!row.some((c) => (c || "").trim())) return;
    const number = at(row, "oz");
    const text = at(row, "short_text");
    const menge = at(row, "quantity");
    const level = kgLevel(number);
    const kind: GaebEntry["kind"] = level > 0 ? "kg" : menge ? "position" : "entry";
    if (!number && !text && !menge) return;
    out.push({
      kind,
      number,
      text,
      menge_einheit: menge,
      teilbetrag_ep: at(row, "teilbetrag"),
      gesamt: at(row, "total"),
      level,
      kg: [],
      long_text: null,
    });
  });
  return out;
}

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
  const [rows, setRows] = useState<GaebEntry[] | null>(null);
  // The file as extracted, and which column we take to be what. Kept so the assignment can be
  // corrected without re-uploading.
  const [grid, setGrid] = useState<string[][]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [startAt, setStartAt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function update(i: number, field: keyof GaebEntry, value: string) {
    setRows((prev) => (prev ? prev.map((r, j) => (j === i ? { ...r, [field]: value } : r)) : prev));
  }

  function removeRow(i: number) {
    setRows((prev) => (prev ? prev.filter((_, j) => j !== i) : prev));
  }

  function addRow() {
    setRows((prev) => [...(prev ?? []), { ...EMPTY }]);
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
        setRows(null);
        return;
      }
      const boq = data as GaebBoQ;
      setProjectName(base);
      const raw = boq.grid ?? [];
      setGrid(raw);
      setStartAt(boq.data_starts_at ?? 0);
      if (raw.length) {
        // Start from the server's guess, then let the user correct it.
        const width = Math.max(...raw.map((r) => r.length));
        const assigned: Role[] = Array.from({ length: width }, () => "" as Role);
        for (const [role, index] of Object.entries(boq.mapping ?? {})) {
          if ((ROLES as readonly string[]).includes(role) && index < width && !assigned[index]) {
            assigned[index] = role as Role;
          }
        }
        setRoles(assigned);
        setRows(rowsFromGrid(raw, assigned, boq.data_starts_at ?? 0));
      } else {
        setRoles([]);
        // The document as it stands — headings, source entries and positions, in order.
        setRows(boq.entries?.length ? boq.entries : []);
      }
    } catch {
      setError("The server could not be reached. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  }

  async function exportX84() {
    if (!rows) return;
    setError(null);
    setBusy(true);
    try {
      const res = await fetch("/api/gaeb/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_name: projectName, currency: "EUR", entries: rows }),
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

  // Quantity × unit price is never displayed or exported — the file's own figures are. It is
  // computed here for one purpose: if it disagrees with the total the file printed, a column was
  // read wrongly, and the row is marked so a person looks at it.
  function mismatched(r: GaebEntry): boolean {
    if (r.kind !== "position" || !r.gesamt || !r.teilbetrag_ep) return false;
    return (
      Math.abs(toNumber(r.menge_einheit) * toNumber(r.teilbetrag_ep) - toNumber(r.gesamt)) > 0.02
    );
  }
  const mismatches = (rows ?? []).filter(mismatched).length;

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
          {busy && !rows ? t.converting : t.convert}
        </button>
      </div>

      {error && <p className="mt-4 text-sm text-red-600 dark:text-red-400">{error}</p>}

      {rows && (
        <div className="mt-6">
          <p className="mb-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-300">
            {t.verifyNote}
          </p>

          {grid.length > 0 && (
            <details className="mb-3 rounded-md border border-neutral-200 p-3 dark:border-neutral-800">
              <summary className="cursor-pointer text-xs text-neutral-500">{t.mapperTitle}</summary>
              <p className="mt-2 text-xs text-neutral-500">{t.mapperHint}</p>
              <div className="mt-2 overflow-x-auto">
                <table className="text-xs">
                  <thead>
                    <tr>
                      {roles.map((role, c) => (
                        <th key={c} className="p-1 text-left">
                          <select
                            value={role}
                            onChange={(e) => {
                              const next = [...roles];
                              next[c] = e.target.value as Role;
                              setRoles(next);
                              setRows(rowsFromGrid(grid, next, startAt));
                            }}
                            className="rounded border border-neutral-300 bg-transparent px-1 py-0.5 dark:border-neutral-700 dark:bg-neutral-900"
                          >
                            <option value="">{t.roleIgnore}</option>
                            <option value="oz">{t.colKgOz}</option>
                            <option value="short_text">{t.roleText}</option>
                            <option value="quantity">{t.colQty}</option>
                            <option value="teilbetrag">{t.colTeilbetrag}</option>
                            <option value="total">{t.colTotal}</option>
                          </select>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {/* The first rows exactly as extracted, so the assignment can be seen to be right. */}
                    {grid.slice(0, 6).map((row, r) => (
                      <tr key={r} className="border-t border-neutral-100 dark:border-neutral-900">
                        {roles.map((_, c) => (
                          <td
                            key={c}
                            title={row[c] ?? ""}
                            className="max-w-[16rem] truncate p-1 text-neutral-500"
                          >
                            {row[c] ?? ""}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <label className="mt-2 flex items-center gap-2 text-xs text-neutral-500">
                {t.dataStartsAt}
                <input
                  type="number"
                  min={0}
                  value={startAt}
                  onChange={(e) => {
                    const n = Math.max(0, Number(e.target.value) || 0);
                    setStartAt(n);
                    setRows(rowsFromGrid(grid, roles, n));
                  }}
                  className="w-16 rounded border border-neutral-300 bg-transparent px-1 py-0.5 dark:border-neutral-700"
                />
              </label>
            </details>
          )}

          {mismatches > 0 && (
            <p className="mb-3 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950 dark:text-red-300">
              {t.mismatchWarning(mismatches)}
            </p>
          )}

          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                {/* The source document's own columns, in its own order. */}
                <tr className="border-b border-neutral-200 text-left text-xs text-neutral-500 dark:border-neutral-800">
                  <th className="py-2 pr-2">{t.colKgOz}</th>
                  <th className="py-2 pr-2">{t.colText}</th>
                  <th className="py-2 pr-2 text-right">{t.colQty}</th>
                  <th className="py-2 pr-2 text-right">{t.colTeilbetrag}</th>
                  <th className="py-2 pr-2 text-right">{t.colTotal}</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const heading = r.kind !== "position";
                  return (
                    <tr
                      key={i}
                      className={`border-b border-neutral-100 align-top dark:border-neutral-900 ${
                        heading ? "bg-neutral-50 font-medium dark:bg-neutral-900/40" : ""
                      }`}
                    >
                      {/* KG / OZ — indented by its level, so the hierarchy reads as it does in the
                          source document. */}
                      <td className="py-1 pr-2 whitespace-nowrap">
                        <input
                          value={r.number}
                          onChange={(e) => update(i, "number", e.target.value)}
                          style={{ paddingLeft: `${(r.level ? r.level - 1 : 3) * 0.75}rem` }}
                          className="w-32 rounded border border-transparent bg-transparent px-1 py-0.5 hover:border-neutral-200 dark:hover:border-neutral-800"
                        />
                      </td>
                      <td className="w-1/2 py-1 pr-2">
                        {/* A textarea, not a single-line input: a Leistungstext runs to a full
                            sentence and was being clipped at the width of the box. It wraps to as
                            many lines as it needs, so the whole text is readable — the point of the
                            table is to show what the document says. */}
                        <textarea
                          value={r.text}
                          onChange={(e) => update(i, "text", e.target.value)}
                          rows={Math.max(1, Math.ceil(r.text.length / 55))}
                          className="w-full min-w-[22rem] resize-y rounded border border-transparent bg-transparent px-1 py-0.5 leading-snug hover:border-neutral-200 dark:hover:border-neutral-800"
                        />
                        {/* The Langtext — where a German LV prints its DIN references. */}
                        {r.long_text && (
                          <textarea
                            value={r.long_text}
                            onChange={(e) => update(i, "long_text", e.target.value)}
                            rows={Math.min(6, r.long_text.split("\n").length + 1)}
                            className="mt-1 w-full min-w-[18rem] rounded border border-neutral-200 bg-transparent px-1 py-0.5 text-xs text-neutral-500 dark:border-neutral-800"
                          />
                        )}
                      </td>
                      {/* Menge/Einheit, Teilbetrag / EP and Gesamt EUR exactly as printed. */}
                      <td className="py-1 pr-2 text-right">
                        <input
                          value={r.menge_einheit}
                          onChange={(e) => update(i, "menge_einheit", e.target.value)}
                          className="w-24 rounded border border-transparent bg-transparent px-1 py-0.5 text-right hover:border-neutral-200 dark:hover:border-neutral-800"
                        />
                      </td>
                      <td className="py-1 pr-2 text-right">
                        <input
                          value={r.teilbetrag_ep}
                          onChange={(e) => update(i, "teilbetrag_ep", e.target.value)}
                          className="w-28 rounded border border-transparent bg-transparent px-1 py-0.5 text-right hover:border-neutral-200 dark:hover:border-neutral-800"
                        />
                      </td>
                      <td className={`py-1 pr-2 text-right ${mismatched(r) ? "rounded bg-red-50 dark:bg-red-950" : ""}`}>
                        <input
                          value={r.gesamt}
                          onChange={(e) => update(i, "gesamt", e.target.value)}
                          className="w-32 rounded border border-transparent bg-transparent px-1 py-0.5 text-right hover:border-neutral-200 dark:hover:border-neutral-800"
                        />
                      </td>
                      <td className="py-1">
                        <button
                          type="button"
                          onClick={() => removeRow(i)}
                          className="text-xs text-neutral-400 hover:text-red-500"
                        >
                          {t.removeRow}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <button type="button" onClick={addRow} className="text-sm text-neutral-500 underline">
              {t.addRow}
            </button>
            <p className="text-xs text-neutral-400">{t.noSumNote}</p>
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
