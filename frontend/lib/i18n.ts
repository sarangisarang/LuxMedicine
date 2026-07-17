// The words a clinician reads, chosen rather than generated.
//
// `schemas/answer.py` returns an enum for an empty answer and no prose, on purpose: "An enum
// leaves nothing to write into. The wording a clinician reads lives in the UI, where a human
// chose it." That makes this file part of the safety boundary, not a strings bundle — the
// difference between "the guidelines do not cover this" and "we could not verify our own
// answer" is the difference between a fact about the corpus and a fact about us
// malfunctioning, and it must survive translation.
//
// **These strings are NOT machine translations and must never become them.** The German and
// Georgian below need a review by a clinician who reads that language before this ships —
// especially `noAnswer.verification_failed` and everything under `translation`. A
// machine-chosen safety string is the same category of problem as a machine-written answer.

export type UiLang = "en" | "de" | "ka";

export const UI_LANGUAGES: { code: UiLang; label: string }[] = [
  { code: "en", label: "English" },
  { code: "de", label: "Deutsch" },
  { code: "ka", label: "ქართული" },
];

// What `Query.language` is set to — a hint recorded on the query, never a corpus filter.
export const QUERY_LANGUAGE: Record<UiLang, string> = {
  en: "English",
  de: "German",
  ka: "Georgian",
};

type Strings = {
  tagline: string;
  placeholder: string;
  ask: string;
  searching: string;
  noAnswer: {
    no_relevant_sources: string;
    sources_do_not_answer: string;
    verification_failed: string;
    table_not_citable: string;
    fallback: string;
  };
  rejected: (n: number) => string;
  superseded: (label: string | null) => string;
  unreadable: (pages: string) => string;
  pdf: {
    cited: string;
    prev: string;
    next: string;
    close: string;
    page: string;
    of: string;
    loading: string;
    error: (message: string) => string;
  };
  translation: {
    label: string;
    banner: (language: string) => string;
    footnote: string;
    failed: string;
    rateLimited: string;
  };
};

export const STRINGS: Record<UiLang, Strings> = {
  en: {
    tagline:
      "Clinical guideline search. Answers are the guideline's own words, cited to the page — never a summary or a recommendation.",
    placeholder: "e.g. How is chronic kidney disease classified by GFR?",
    ask: "Ask",
    searching: "Searching…",
    noAnswer: {
      no_relevant_sources: "No guideline in the corpus covers this question.",
      sources_do_not_answer:
        "Guidelines were found, but none of their passages answer this question.",
      verification_failed:
        "An answer was produced but could not be verified against its source, so it is being withheld. This is a system fault, not an absence of guidance.",
      // #48: the guideline DOES answer this, in a table, but the answer is a category number
      // whose column heading this system cannot yet quote alongside it — so the number would
      // be shown without what it applies to. Point to the source, do not imply silence.
      table_not_citable:
        "The answer is in a table, and this system cannot yet quote it with the column headings that give its category numbers meaning. Rather than show a number without its heading, it is withheld — open the source page to read the table directly.",
      fallback: "No answer.",
    },
    rejected: (n) =>
      `${n} quote${n === 1 ? " was" : "s were"} rejected as unverifiable and ${n === 1 ? "is" : "are"} not shown.`,
    superseded: (label) =>
      label ? `This edition has been superseded by ${label}.` : "This edition has been superseded.",
    unreadable: (pages) =>
      `Pages ${pages} of this document could not be read and are not reflected above.`,
    pdf: {
      cited: "cited",
      prev: "‹ Prev",
      next: "Next ›",
      close: "Close",
      page: "Page",
      of: "of",
      loading: "Loading source…",
      error: (m) => `Could not load the source PDF: ${m}. Is the backend running?`,
    },
    translation: {
      label: "Translate:",
      banner: (l) => `Machine translation · ${l} · not the guideline's words, not verified`,
      footnote: "The verbatim quote above is the record. Check anything you act on against it.",
      failed: "Translation failed.",
      // An expected condition, not a fault — say so, or a clinician goes looking for a bug.
      rateLimited: "The translation service has reached its request limit. Try again later — the quote above is unaffected.",
    },
  },

  de: {
    tagline:
      "Suche in klinischen Leitlinien. Antworten sind die Worte der Leitlinie selbst, mit Seitenangabe — niemals eine Zusammenfassung und keine Empfehlung.",
    placeholder: "z. B. Wie wird eine chronische Nierenerkrankung nach GFR eingeteilt?",
    ask: "Fragen",
    searching: "Suche läuft…",
    noAnswer: {
      no_relevant_sources: "Keine Leitlinie im Bestand behandelt diese Frage.",
      sources_do_not_answer:
        "Es wurden Leitlinien gefunden, aber keine ihrer Textstellen beantwortet diese Frage.",
      // Deliberately blunt: this says WE failed, not that guidance is missing. The distinction
      // is the point — see the note at the top of this file.
      verification_failed:
        "Eine Antwort wurde erzeugt, konnte aber nicht gegen ihre Quelle geprüft werden und wird deshalb zurückgehalten. Dies ist ein Fehler des Systems, kein Fehlen einer Leitlinienaussage.",
      // TODO(#48): clinician-facing medical German — have a native speaker review before deploy.
      table_not_citable:
        "Die Antwort steht in einer Tabelle, und dieses System kann sie noch nicht zusammen mit den Spaltenüberschriften zitieren, die den Kategoriezahlen ihre Bedeutung geben. Statt eine Zahl ohne ihre Überschrift anzuzeigen, wird sie zurückgehalten — öffnen Sie die Quellseite, um die Tabelle direkt zu lesen.",
      fallback: "Keine Antwort.",
    },
    rejected: (n) =>
      `${n} Zitat${n === 1 ? "" : "e"} wurde${n === 1 ? "" : "n"} als nicht überprüfbar verworfen und ${n === 1 ? "wird" : "werden"} nicht angezeigt.`,
    superseded: (label) =>
      label
        ? `Diese Ausgabe wurde durch ${label} ersetzt.`
        : "Diese Ausgabe wurde ersetzt.",
    unreadable: (pages) =>
      `Die Seiten ${pages} dieses Dokuments konnten nicht gelesen werden und sind oben nicht berücksichtigt.`,
    pdf: {
      cited: "zitiert",
      prev: "‹ Zurück",
      next: "Weiter ›",
      close: "Schließen",
      page: "Seite",
      of: "von",
      loading: "Quelle wird geladen…",
      error: (m) => `Quell-PDF konnte nicht geladen werden: ${m}. Läuft das Backend?`,
    },
    translation: {
      label: "Übersetzen:",
      banner: (l) =>
        `Maschinelle Übersetzung · ${l} · nicht die Worte der Leitlinie, nicht geprüft`,
      footnote:
        "Maßgeblich ist das wörtliche Zitat oben. Prüfen Sie alles, wonach Sie handeln, dort nach.",
      failed: "Übersetzung fehlgeschlagen.",
      rateLimited:
        "Der Übersetzungsdienst hat sein Anfragelimit erreicht. Versuchen Sie es später erneut — das Zitat oben ist davon nicht betroffen.",
    },
  },

  ka: {
    tagline:
      "კლინიკური გაიდლაინების ძიება. პასუხი გაიდლაინის საკუთარი სიტყვებია, გვერდის მითითებით — არასდროს შეჯამება და არც რეკომენდაცია.",
    placeholder: "მაგ. როგორ კლასიფიცირდება თირკმლის ქრონიკული დაავადება GFR-ით?",
    ask: "კითხვა",
    searching: "მიმდინარეობს ძიება…",
    noAnswer: {
      no_relevant_sources: "კორპუსში არცერთი გაიდლაინი არ ეხება ამ კითხვას.",
      sources_do_not_answer:
        "გაიდლაინები მოიძებნა, მაგრამ არცერთი მათი ნაწყვეტი არ პასუხობს ამ კითხვას.",
      verification_failed:
        "პასუხი შეიქმნა, მაგრამ ვერ გადამოწმდა თავის წყაროსთან, ამიტომ არ ჩვენდება. ეს სისტემის ხარვეზია, არა გაიდლაინის მითითების არარსებობა.",
      table_not_citable:
        "პასუხი ცხრილშია, და სისტემას ჯერ არ შეუძლია ის ციტირდეს იმ სვეტის სათაურებთან ერთად, რომლებიც კატეგორიის რიცხვებს აზრს ანიჭებენ. იმის ნაცვლად, რომ რიცხვი სათაურის გარეშე აჩვენოს, ის დაფარულია — გახსენით წყაროს გვერდი და წაიკითხეთ ცხრილი პირდაპირ.",
      fallback: "პასუხი არ არის.",
    },
    rejected: (n) => `${n} ციტატა უარყოფილია როგორც გადაუმოწმებელი და არ ჩვენდება.`,
    superseded: (label) =>
      label ? `ეს გამოცემა შეცვლილია ${label}-ით.` : "ეს გამოცემა შეცვლილია.",
    unreadable: (pages) =>
      `ამ დოკუმენტის გვერდები ${pages} ვერ წაიკითხა და ზემოთ არ არის ასახული.`,
    pdf: {
      cited: "ციტირებულია",
      prev: "‹ წინა",
      next: "შემდეგი ›",
      close: "დახურვა",
      page: "გვერდი",
      of: "/",
      loading: "წყარო იტვირთება…",
      error: (m) => `წყაროს PDF ვერ ჩაიტვირთა: ${m}. backend მუშაობს?`,
    },
    translation: {
      label: "თარგმნა:",
      banner: (l) => `მანქანური თარგმანი · ${l} · არა გაიდლაინის სიტყვები, არა გადამოწმებული`,
      footnote:
        "ავტორიტეტულია ზემოთ მოცემული სიტყვასიტყვითი ციტატა. ყველაფერი, რაზეც მოქმედებ, იქ გადაამოწმე.",
      failed: "თარგმანი ვერ შესრულდა.",
      rateLimited:
        "თარგმანის სერვისმა მოთხოვნების ლიმიტს მიაღწია. სცადე მოგვიანებით — ზემოთ მოცემულ ციტატას ეს არ ეხება.",
    },
  },
};
