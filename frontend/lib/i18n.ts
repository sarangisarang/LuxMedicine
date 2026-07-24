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

// Passed to `unreadable()` when the caller renders the page numbers itself rather than
// printing them. Splitting the returned sentence on this marker keeps each language's word
// order intact — German puts the list after "Die Seiten", Georgian puts it mid-sentence — so
// the numbers become clickable without any translation being cut into fragments, which is how
// a sentence gets reassembled wrong in the one language nobody here reads.
//
// Deliberately visible ASCII rather than a control character: an invisible marker that leaks
// into the UI is unfindable by eye and by grep. This project has already lost time to a U+0001
// that two greps could not see.
export const PAGES_SLOT = "{{pages}}";

export type UiLang =
  | "en" | "de" | "ka" | "ru" | "uk" | "fr" | "es" | "it"
  | "pl" | "tr" | "ar" | "pt" | "nl" | "ro" | "el" | "cs" | "hu";

// Native names, so a speaker recognises their own language in the list.
export const UI_LANGUAGES: { code: UiLang; label: string }[] = [
  { code: "en", label: "English" },
  { code: "de", label: "Deutsch" },
  { code: "ka", label: "ქართული" },
  { code: "ru", label: "Русский" },
  { code: "uk", label: "Українська" },
  { code: "fr", label: "Français" },
  { code: "es", label: "Español" },
  { code: "it", label: "Italiano" },
  { code: "pl", label: "Polski" },
  { code: "tr", label: "Türkçe" },
  { code: "ar", label: "العربية" },
  { code: "pt", label: "Português" },
  { code: "nl", label: "Nederlands" },
  { code: "ro", label: "Română" },
  { code: "el", label: "Ελληνικά" },
  { code: "cs", label: "Čeština" },
  { code: "hu", label: "Magyar" },
];

// What `Query.language` is set to — the source language the backend translates the query FROM into
// the corpus's language before retrieval (see services/query_translation). A hint on the query,
// never a corpus filter. English names because that is what the translator's target_language wants.
export const QUERY_LANGUAGE: Record<UiLang, string> = {
  en: "English",
  de: "German",
  ka: "Georgian",
  ru: "Russian",
  uk: "Ukrainian",
  fr: "French",
  es: "Spanish",
  it: "Italian",
  pl: "Polish",
  tr: "Turkish",
  ar: "Arabic",
  pt: "Portuguese",
  nl: "Dutch",
  ro: "Romanian",
  el: "Greek",
  cs: "Czech",
  hu: "Hungarian",
};

// Which corpus a question is asked of. Mirrors the backend's Sector enum — the values are sent
// as-is and the backend rejects anything else, so this list cannot widen the search.
export type Sector = "medical" | "legal";

export const SECTORS: { code: Sector; label: Record<UiLang, string> }[] = [
  {
    code: "medical",
    label: {
      en: "Medicine", de: "Medizin", ka: "მედიცინა", ru: "Медицина", uk: "Медицина",
      fr: "Médecine", es: "Medicina", it: "Medicina", pl: "Medycyna", tr: "Tıp",
      ar: "الطب", pt: "Medicina", nl: "Geneeskunde", ro: "Medicină", el: "Ιατρική",
      cs: "Medicína", hu: "Orvostudomány",
    },
  },
  {
    code: "legal",
    label: {
      en: "Law (Baurecht)", de: "Recht (Baurecht)", ka: "სამართალი", ru: "Право", uk: "Право",
      fr: "Droit", es: "Derecho", it: "Diritto", pl: "Prawo", tr: "Hukuk",
      ar: "القانون", pt: "Direito", nl: "Recht", ro: "Drept", el: "Δίκαιο",
      cs: "Právo", hu: "Jog",
    },
  },
];

type Strings = {
  tagline: string;
  placeholder: string;
  ask: string;
  searching: string;
  signOut: string;
  demoNotice: string;

  // Shown only in the legal sector. This is a safety string in the same sense as
  // `noAnswer.verification_failed`: the system returns verbatim statute text with a citation,
  // which reads exactly like an answer to "what am I allowed to bill" — and it is not one.
  // Quoting §35 HOAI correctly says nothing about whether §35 applies to this contract.
  legalNotice: string;

  // The fee tables are extracted as bare number grids whose Honorarzone column headings are in
  // a header row no chunk carries, so the table guard refuses to quote them (see
  // services/table_guard.py). Saying so is the difference between "the statute is silent" and
  // "we will not show you this number without its heading" — the same distinction the
  // noAnswer strings exist to preserve.
  legalTableNotice: string;

  sectorLabel: string;
  noAnswer: {
    no_relevant_sources: string;
    sources_do_not_answer: string;
    verification_failed: string;
    table_not_citable: string;
    fallback: string;
    // Shown only when the corpus did not answer (not on our own faults), because rephrasing is
    // what can help there and cannot help a verification failure.
    hint: string;
    // Named on a DECLINE only. An answered result already says this per source. Without it,
    // "the guideline does not cover this" and "the page that covers it could not be read"
    // are the same sentence, and they are opposite instructions to a clinician.
    incompleteSources: (sources: string[]) => string;
  };
  // A failed request, told apart by cause: an auth/token problem vs the server being down are
  // different actions for the user, and neither is "no answer".
  errors: {
    auth: string;
    server: string;
    request: (detail: string) => string;
  };
  register: {
    heading: string;
    intro: string;
    code: string;
    codeHint: string;
    email: string;
    password: string;
    name: string;
    submit: string;
    submitting: string;
    success: string;
    errors: {
      code: string;
      username: string;
      validation: string;
      server: string;
    };
  };
  rejected: (n: number) => string;
  superseded: (label: string | null) => string;
  // Called with the joined page list for plain text, or with PAGES_SLOT when the caller wants
  // to render the numbers itself (see PAGES_SLOT) — every translation must interpolate its
  // argument exactly once for that to work.
  unreadable: (pages: string) => string;
  // Against a single quote whose OWN page lost text. Distinct from `unreadable`, which lists
  // a document's damaged pages as a set: a page lost entirely yields no quote, so that note
  // describes an absence. This describes a presence — a partly-read page still answering.
  damagedPageQuote: string;
  combined: {
    heading: string;
    // The load-bearing label: this is NOT a summary. It is the same verbatim quotes gathered,
    // each still its own span with its own page — never joined into one statement.
    note: string;
    pageRef: (org: string, pages: string) => string;
  };
  pdf: {
    cited: string;
    // Header label when the viewer was opened at a page nothing was quoted from — an
    // unreadable page. It must NOT say "cited": no quote came from there, that is the point
    // of opening it.
    unreadablePage: string;
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

// The reviewed languages: English, and the German/Georgian that (per the header) still await a
// native clinician review. The other languages below inherit these strings and translate only the
// low-risk chrome — never the safety strings.
const REVIEWED: Record<"en" | "de" | "ka", Strings> = {
  en: {
    tagline:
      "Clinical guideline search. Answers are the guideline's own words, cited to the page — never a summary or a recommendation.",
    placeholder: "e.g. How is chronic kidney disease classified by GFR?",
    ask: "Ask",
    searching: "Searching…",
    signOut: "Sign out",
    demoNotice:
      "Demo — do not enter real patient data. Inference runs in the EU (europe-west3), but the answers are not a substitute for a clinician's judgement.",
    legalNotice:
      "Not legal advice. These are the statute's own words, cited to the page. Whether a provision applies to your contract is a question for a lawyer.",
    legalTableNotice:
      "The fee tables (Honorartafeln) are not quoted: their column headings are not carried with the rows, so a figure could be shown under the wrong Honorarzone. Ask for the rule rather than the amount.",
    sectorLabel: "Corpus",
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
      hint: "Try a more specific question, or ask it the way these guidelines are written — for example, which contraceptive method suits a particular condition, rather than how to treat pain in general.",
      incompleteSources: (sources) =>
        `Note: part of what was searched could not be read. Pages could not be extracted from ${sources.join(", ")} — the number in brackets is how many. This does not mean the answer was on one of them, but the source behind this reply is incomplete, so a silence here is weaker evidence than it looks.`,
    },
    errors: {
      auth: "Your session could not be authenticated. Sign in again, or check that the login service is running.",
      server: "The server did not respond. It may be starting up or down — wait a moment and try again.",
      request: (detail) => `The request could not be processed: ${detail}`,
    },
    register: {
      heading: "Create your account",
      intro: "Registration is by invitation. Enter the code your clinic administrator gave you, and it will place you in your clinic.",
      code: "Invite code",
      codeHint: "Required. It decides which clinic your account belongs to.",
      email: "Email",
      password: "Password",
      name: "Name (optional)",
      submit: "Create account",
      submitting: "Creating…",
      success: "Your account is ready. You can now sign in with your username and password.",
      errors: {
        code: "That invite code is invalid or has expired. Check it with your clinic administrator.",
        username: "That username is already taken. Choose another.",
        validation: "Please check the form — an email and a code are required.",
        server: "Registration could not be completed right now. Please try again in a moment.",
      },
    },
    rejected: (n) =>
      `${n} quote${n === 1 ? " was" : "s were"} rejected as unverifiable and ${n === 1 ? "is" : "are"} not shown.`,
    superseded: (label) =>
      label ? `This edition has been superseded by ${label}.` : "This edition has been superseded.",
    unreadable: (pages) =>
      `Pages ${pages} of this document could not be read and are not reflected above.`,
    damagedPageQuote:
      "Part of this page could not be read, so a qualifier printed beside this passage may be missing from it. Open the source page before relying on it.",
    combined: {
      heading: "Full text — the guideline's own words, gathered",
      note: "The same verbatim quotes above, collected here to read in one place. Not a summary and not joined into a single statement — each is a separate span, kept with its page.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
    pdf: {
      cited: "cited",
      unreadablePage: "unreadable page",
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
    signOut: "Abmelden",
    demoNotice:
      "Demo — keine echten Patientendaten eingeben. Die Verarbeitung erfolgt in der EU (europe-west3), aber die Antworten ersetzen nicht das ärztliche Urteil.",
    legalNotice:
      "Keine Rechtsberatung. Dies sind die Worte der Vorschrift selbst, mit Seitenangabe. Ob eine Regelung auf Ihren Vertrag anwendbar ist, ist eine Frage für einen Rechtsanwalt.",
    legalTableNotice:
      "Die Honorartafeln werden nicht zitiert: Die Spaltenüberschriften werden nicht mit den Zeilen übernommen, sodass ein Betrag unter der falschen Honorarzone erscheinen könnte. Fragen Sie nach der Regelung, nicht nach dem Betrag.",
    sectorLabel: "Bestand",
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
      hint: "Stellen Sie die Frage spezifischer, oder so, wie diese Leitlinien geschrieben sind — zum Beispiel, welche Verhütungsmethode zu einer bestimmten Erkrankung passt, statt wie man Schmerzen allgemein behandelt.",
      incompleteSources: (sources) =>
        `Hinweis: Ein Teil des Durchsuchten konnte nicht gelesen werden. Aus ${sources.join(", ")} ließen sich Seiten nicht extrahieren — die Zahl in Klammern gibt an, wie viele. Das heißt nicht, dass die Antwort auf einer davon stand; die Quelle hinter dieser Auskunft ist jedoch unvollständig, sodass ein Schweigen hier weniger aussagt, als es scheint.`,
    },
    // TODO(review): clinician-facing German — have a native speaker check before deploy.
    errors: {
      auth: "Ihre Sitzung konnte nicht authentifiziert werden. Melden Sie sich erneut an, oder prüfen Sie, ob der Anmeldedienst läuft.",
      server: "Der Server hat nicht geantwortet. Er startet möglicherweise gerade oder ist nicht erreichbar — warten Sie einen Moment und versuchen Sie es erneut.",
      request: (detail) => `Die Anfrage konnte nicht verarbeitet werden: ${detail}`,
    },
    // TODO(review): clinician-facing German — have a native speaker check before deploy.
    register: {
      heading: "Konto erstellen",
      intro: "Die Registrierung erfolgt auf Einladung. Geben Sie den Code Ihrer Klinikadministration ein — er ordnet Sie Ihrer Klinik zu.",
      code: "Einladungscode",
      codeHint: "Erforderlich. Er bestimmt, zu welcher Klinik Ihr Konto gehört.",
      email: "E-Mail",
      password: "Passwort",
      name: "Name (optional)",
      submit: "Konto erstellen",
      submitting: "Wird erstellt…",
      success: "Ihr Konto ist bereit. Sie können sich jetzt mit Ihrem Benutzernamen und Passwort anmelden.",
      errors: {
        code: "Dieser Einladungscode ist ungültig oder abgelaufen. Prüfen Sie ihn mit Ihrer Klinikadministration.",
        username: "Dieser Benutzername ist bereits vergeben. Wählen Sie einen anderen.",
        validation: "Bitte prüfen Sie das Formular — eine E-Mail und ein Code sind erforderlich.",
        server: "Die Registrierung konnte gerade nicht abgeschlossen werden. Bitte versuchen Sie es gleich erneut.",
      },
    },
    rejected: (n) =>
      `${n} Zitat${n === 1 ? "" : "e"} wurde${n === 1 ? "" : "n"} als nicht überprüfbar verworfen und ${n === 1 ? "wird" : "werden"} nicht angezeigt.`,
    superseded: (label) =>
      label
        ? `Diese Ausgabe wurde durch ${label} ersetzt.`
        : "Diese Ausgabe wurde ersetzt.",
    unreadable: (pages) =>
      `Die Seiten ${pages} dieses Dokuments konnten nicht gelesen werden und sind oben nicht berücksichtigt.`,
    damagedPageQuote:
      "Ein Teil dieser Seite konnte nicht gelesen werden; eine einschränkende Angabe neben dieser Textstelle könnte daher fehlen. Öffnen Sie die Quellseite, bevor Sie sich darauf stützen.",
    // TODO(review): clinician-facing medical German — have a native speaker check before deploy.
    combined: {
      heading: "Gesamter Text — die Worte der Leitlinie, gesammelt",
      note: "Dieselben wörtlichen Zitate von oben, hier an einer Stelle gesammelt. Keine Zusammenfassung und nicht zu einer einzigen Aussage verbunden — jedes ist ein eigener Abschnitt, mit seiner Seite.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
    pdf: {
      cited: "zitiert",
      unreadablePage: "nicht lesbare Seite",
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
    signOut: "გასვლა",
    demoNotice:
      "დემო — ნუ შეიყვანთ რეალურ პაციენტის მონაცემებს. დამუშავება EU-ში ხდება (europe-west3), მაგრამ პასუხები ექიმის მსჯელობას არ ცვლის.",
    legalNotice:
      "ეს არ არის იურიდიული კონსულტაცია. ეს თავად ნორმის სიტყვებია, გვერდის მითითებით. ვრცელდება თუ არა ნორმა თქვენს ხელშეკრულებაზე — ეს ადვოკატის საკითხია.",
    legalTableNotice:
      "ჰონორარის ცხრილები (Honorartafeln) არ ციტირდება: სვეტების სათაურები არ გადმოჰყვება სტრიქონებს, ამიტომ თანხა შეიძლება არასწორ Honorarzone-ს მიეწეროს. იკითხეთ ნორმა, არა თანხა.",
    sectorLabel: "კორპუსი",
    noAnswer: {
      no_relevant_sources: "კორპუსში არცერთი გაიდლაინი არ ეხება ამ კითხვას.",
      sources_do_not_answer:
        "გაიდლაინები მოიძებნა, მაგრამ არცერთი მათი ნაწყვეტი არ პასუხობს ამ კითხვას.",
      verification_failed:
        "პასუხი შეიქმნა, მაგრამ ვერ გადამოწმდა თავის წყაროსთან, ამიტომ არ ჩვენდება. ეს სისტემის ხარვეზია, არა გაიდლაინის მითითების არარსებობა.",
      table_not_citable:
        "პასუხი ცხრილშია, და სისტემას ჯერ არ შეუძლია ის ციტირდეს იმ სვეტის სათაურებთან ერთად, რომლებიც კატეგორიის რიცხვებს აზრს ანიჭებენ. იმის ნაცვლად, რომ რიცხვი სათაურის გარეშე აჩვენოს, ის დაფარულია — გახსენით წყაროს გვერდი და წაიკითხეთ ცხრილი პირდაპირ.",
      fallback: "პასუხი არ არის.",
      hint: "სცადე უფრო კონკრეტული კითხვა, ან ისე დასვი, როგორც ეს გაიდლაინებია დაწერილი — მაგალითად, რომელი კონტრაცეპტივი შეეფერება კონკრეტულ მდგომარეობას, და არა როგორ ვუმკურნალო ტკივილს ზოგადად.",
      incompleteSources: (sources) =>
        `შენიშვნა: მოძიებულის ნაწილი ვერ წაიკითხა. ${sources.join(", ")} — აქედან გვერდები ვერ ამოიღო (ფრჩხილებში მათი რაოდენობაა). ეს არ ნიშნავს, რომ პასუხი სწორედ იქ იყო — მაგრამ ამ პასუხის უკან მდგარი წყარო არასრულია, ანუ აქ სიჩუმე უფრო სუსტი მტკიცებულებაა, ვიდრე ჩანს.`,
    },
    errors: {
      auth: "სესია ვერ დამოწმდა. თავიდან შედი, ან შეამოწმე, მუშაობს თუ არა ავტორიზაციის სერვისი.",
      server: "სერვერმა არ უპასუხა. შესაძლოა ეშვება ან გამორთულია — მოიცადე წამით და თავიდან სცადე.",
      request: (detail) => `მოთხოვნა ვერ დამუშავდა: ${detail}`,
    },
    register: {
      heading: "შექმენი ანგარიში",
      intro: "რეგისტრაცია მოსაწვევითაა. შეიყვანე კოდი, რომელიც კლინიკის ადმინისტრატორმა მოგცა — ის შენს კლინიკაში მოგათავსებს.",
      code: "მოსაწვევი კოდი",
      codeHint: "სავალდებულო. ის განსაზღვრავს, რომელ კლინიკას ეკუთვნის შენი ანგარიში.",
      email: "ელფოსტა",
      password: "პაროლი",
      name: "სახელი (არასავალდებულო)",
      submit: "ანგარიშის შექმნა",
      submitting: "იქმნება…",
      success: "შენი ანგარიში მზადაა. ახლა შეგიძლია შეხვიდე მომხმარებლის სახელითა და პაროლით.",
      errors: {
        code: "ეს მოსაწვევი კოდი არასწორია ან ვადაგასულია. გადაამოწმე კლინიკის ადმინისტრატორთან.",
        username: "ეს მომხმარებლის სახელი დაკავებულია. აირჩიე სხვა.",
        validation: "შეამოწმე ფორმა — ელფოსტა და კოდი სავალდებულოა.",
        server: "რეგისტრაცია ახლა ვერ დასრულდა. სცადე ცოტა ხანში.",
      },
    },
    rejected: (n) => `${n} ციტატა უარყოფილია როგორც გადაუმოწმებელი და არ ჩვენდება.`,
    superseded: (label) =>
      label ? `ეს გამოცემა შეცვლილია ${label}-ით.` : "ეს გამოცემა შეცვლილია.",
    unreadable: (pages) =>
      `ამ დოკუმენტის გვერდები ${pages} ვერ წაიკითხა და ზემოთ არ არის ასახული.`,
    damagedPageQuote:
      "ამ გვერდის ნაწილი ვერ წაიკითხა — ამ ნაწყვეტის გვერდით დაბეჭდილი დამაზუსტებელი პირობა შესაძლოა აკლდეს. დაეყრდნობამდე გახსენი წყაროს გვერდი.",
    combined: {
      heading: "სრული ტექსტი — გაიდლაინის სიტყვები, თავმოყრილი",
      note: "ზემოთ მოცემული იგივე ვერბატიმ ციტატები, ერთ ადგილას შეკრებილი წასაკითხად. არა შეჯამება და არა ერთ დებულებად გაერთიანებული — თითოეული ცალკე ნაწყვეტია, თავის გვერდთან ერთად.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
    pdf: {
      cited: "ციტირებულია",
      unreadablePage: "წაუკითხავი გვერდი",
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

// Low-risk interface chrome for the additional languages. The safety-critical strings (noAnswer.*,
// translation.*, errors, and the notices) are DELIBERATELY absent — they fall back to English until
// a clinician who reads the language reviews them, exactly as the file header requires. A
// machine-translated safety string is the same hazard as a machine-written answer.
// The always-visible / high-frequency strings. Nested and safety-subtle strings (noAnswer.*,
// translation.*, errors, register, pdf) still fall back to English pending native review — the
// notices here are warnings that must be UNDERSTOOD, so translating them (carefully) is safer than
// leaving a non-English speaker unable to read "do not enter real patient data". Still not native-
// reviewed; flag before real clinical use.
type Chrome = Pick<
  Strings,
  "tagline" | "placeholder" | "ask" | "searching" | "signOut" | "sectorLabel"
> &
  Partial<Pick<Strings, "demoNotice" | "legalNotice">>;

const CHROME: Record<Exclude<UiLang, "en" | "de" | "ka">, Chrome> = {
  ru: {
    tagline: "Поиск по клиническим руководствам. Ответы — это дословные слова руководства со ссылкой на страницу, никогда не резюме и не рекомендация.",
    placeholder: "например: Как классифицируется хроническая болезнь почек по СКФ?",
    ask: "Спросить", searching: "Поиск…", signOut: "Выйти", sectorLabel: "Корпус",
  },
  uk: {
    tagline: "Пошук у клінічних настановах. Відповіді — це дослівні слова настанови з посиланням на сторінку, ніколи не резюме та не рекомендація.",
    placeholder: "наприклад: Як класифікують хронічну хворобу нирок за ШКФ?",
    ask: "Запитати", searching: "Пошук…", signOut: "Вийти", sectorLabel: "Корпус",
  },
  fr: {
    tagline: "Recherche dans les recommandations cliniques. Les réponses sont les propres mots de la recommandation, cités à la page — jamais un résumé ni une recommandation.",
    placeholder: "ex. : Comment la maladie rénale chronique est-elle classée selon le DFG ?",
    ask: "Demander", searching: "Recherche…", signOut: "Se déconnecter", sectorLabel: "Corpus",
  },
  es: {
    tagline: "Búsqueda en guías clínicas. Las respuestas son las propias palabras de la guía, citadas a la página, nunca un resumen ni una recomendación.",
    placeholder: "p. ej.: ¿Cómo se clasifica la enfermedad renal crónica según el FG?",
    ask: "Preguntar", searching: "Buscando…", signOut: "Cerrar sesión", sectorLabel: "Corpus",
  },
  it: {
    tagline: "Ricerca nelle linee guida cliniche. Le risposte sono le parole stesse della linea guida, citate alla pagina, mai un riassunto né una raccomandazione.",
    placeholder: "es.: Come si classifica la malattia renale cronica secondo il GFR?",
    ask: "Chiedi", searching: "Ricerca…", signOut: "Esci", sectorLabel: "Corpus",
  },
  pl: {
    tagline: "Wyszukiwanie w wytycznych klinicznych. Odpowiedzi to własne słowa wytycznej, z odniesieniem do strony — nigdy streszczenie ani zalecenie.",
    placeholder: "np. Jak klasyfikuje się przewlekłą chorobę nerek według GFR?",
    ask: "Zapytaj", searching: "Szukanie…", signOut: "Wyloguj", sectorLabel: "Korpus",
  },
  tr: {
    tagline: "Klinik kılavuz araması. Yanıtlar, kılavuzun kendi sözleridir ve sayfaya atıf yapılır — asla bir özet ya da öneri değildir.",
    placeholder: "örn.: Kronik böbrek hastalığı GFR'ye göre nasıl sınıflandırılır?",
    ask: "Sor", searching: "Aranıyor…", signOut: "Çıkış yap", sectorLabel: "Külliyat",
  },
  ar: {
    tagline: "البحث في الإرشادات السريرية. الإجابات هي كلمات الإرشاد نفسها، مقتبسة مع الإشارة إلى الصفحة — وليست ملخصًا ولا توصية أبدًا.",
    placeholder: "مثال: كيف يُصنَّف مرض الكلى المزمن حسب معدل الترشيح الكبيبي؟",
    ask: "اسأل", searching: "جارٍ البحث…", signOut: "تسجيل الخروج", sectorLabel: "المجموعة",
  },
  pt: {
    tagline: "Pesquisa em diretrizes clínicas. As respostas são as próprias palavras da diretriz, citadas à página — nunca um resumo nem uma recomendação.",
    placeholder: "ex.: Como a doença renal crónica é classificada pela TFG?",
    ask: "Perguntar", searching: "A pesquisar…", signOut: "Sair", sectorLabel: "Corpus",
  },
  nl: {
    tagline: "Zoeken in klinische richtlijnen. Antwoorden zijn de eigen woorden van de richtlijn, met paginaverwijzing — nooit een samenvatting of aanbeveling.",
    placeholder: "bijv.: Hoe wordt chronische nierziekte geclassificeerd volgens GFR?",
    ask: "Vragen", searching: "Zoeken…", signOut: "Afmelden", sectorLabel: "Corpus",
  },
  ro: {
    tagline: "Căutare în ghiduri clinice. Răspunsurile sunt cuvintele proprii ale ghidului, citate la pagină — niciodată un rezumat sau o recomandare.",
    placeholder: "ex.: Cum se clasifică boala cronică de rinichi după RFG?",
    ask: "Întreabă", searching: "Se caută…", signOut: "Deconectare", sectorLabel: "Corpus",
  },
  el: {
    tagline: "Αναζήτηση σε κλινικές κατευθυντήριες οδηγίες. Οι απαντήσεις είναι τα ίδια τα λόγια της οδηγίας, με παραπομπή στη σελίδα — ποτέ περίληψη ή σύσταση.",
    placeholder: "π.χ.: Πώς ταξινομείται η χρόνια νεφρική νόσος κατά GFR;",
    ask: "Ρώτησε", searching: "Αναζήτηση…", signOut: "Αποσύνδεση", sectorLabel: "Σώμα κειμένων",
  },
  cs: {
    tagline: "Vyhledávání v klinických doporučeních. Odpovědi jsou vlastní slova doporučení s odkazem na stránku — nikdy shrnutí ani doporučení.",
    placeholder: "např.: Jak se chronické onemocnění ledvin klasifikuje podle GFR?",
    ask: "Zeptat se", searching: "Hledání…", signOut: "Odhlásit se", sectorLabel: "Korpus",
  },
  hu: {
    tagline: "Keresés a klinikai irányelvekben. A válaszok az irányelv saját szavai, oldalhivatkozással — soha nem összefoglaló vagy ajánlás.",
    placeholder: "pl.: Hogyan osztályozzák a krónikus vesebetegséget a GFR szerint?",
    ask: "Kérdez", searching: "Keresés…", signOut: "Kijelentkezés", sectorLabel: "Korpusz",
  },
};

// The two always-visible warning banners, translated so a non-English speaker can actually READ the
// warning. Kept in a separate map (not folded into each CHROME entry) so the two can be filled in one
// place. NOT the safety decline strings (noAnswer.*) — those still await native review.
const NOTICES: Record<Exclude<UiLang, "en" | "de" | "ka">, Pick<Strings, "demoNotice" | "legalNotice">> = {
  ru: { demoNotice: "Демо — не вводите реальные данные пациентов. Вывод выполняется в ЕС (europe-west3), но ответы не заменяют суждение врача.", legalNotice: "Не является юридической консультацией. Это дословные слова закона со ссылкой на страницу. Применима ли норма к вашему договору — вопрос к юристу." },
  uk: { demoNotice: "Демо — не вводьте реальні дані пацієнтів. Обчислення виконуються в ЄС (europe-west3), але відповіді не замінюють судження лікаря.", legalNotice: "Не є юридичною консультацією. Це дослівні слова закону з посиланням на сторінку. Чи застосовується норма до вашого договору — питання до юриста." },
  fr: { demoNotice: "Démo — n'entrez pas de données réelles de patients. L'inférence s'exécute dans l'UE (europe-west3), mais les réponses ne remplacent pas le jugement d'un clinicien.", legalNotice: "Pas un conseil juridique. Ce sont les termes mêmes de la loi, cités à la page. Savoir si une disposition s'applique à votre contrat relève d'un avocat." },
  es: { demoNotice: "Demo — no introduzca datos reales de pacientes. La inferencia se ejecuta en la UE (europe-west3), pero las respuestas no sustituyen el juicio de un clínico.", legalNotice: "No es asesoramiento jurídico. Son las palabras textuales de la ley, citadas a la página. Si una disposición se aplica a su contrato es una cuestión para un abogado." },
  it: { demoNotice: "Demo — non inserire dati reali dei pazienti. L'inferenza viene eseguita nell'UE (europe-west3), ma le risposte non sostituiscono il giudizio del medico.", legalNotice: "Non è consulenza legale. Sono le parole testuali della legge, citate alla pagina. Se una disposizione si applichi al vostro contratto è una questione per un avvocato." },
  pl: { demoNotice: "Demo — nie wprowadzaj rzeczywistych danych pacjentów. Wnioskowanie odbywa się w UE (europe-west3), ale odpowiedzi nie zastępują oceny lekarza.", legalNotice: "To nie porada prawna. To dosłowne słowa ustawy, z odniesieniem do strony. Czy przepis dotyczy Twojej umowy — to pytanie do prawnika." },
  tr: { demoNotice: "Demo — gerçek hasta verisi girmeyin. Çıkarım AB'de çalışır (europe-west3), ancak yanıtlar bir klinisyenin değerlendirmesinin yerini tutmaz.", legalNotice: "Hukuki tavsiye değildir. Bunlar kanunun kendi sözleridir, sayfaya atıfla. Bir hükmün sözleşmenize uygulanıp uygulanmadığı bir avukata sorulur." },
  ar: { demoNotice: "عرض تجريبي — لا تُدخل بيانات مرضى حقيقية. يُجرى الاستدلال في الاتحاد الأوروبي (europe-west3)، لكن الإجابات لا تُغني عن حكم الطبيب.", legalNotice: "ليست استشارة قانونية. هذه كلمات القانون نفسها، مقتبسة مع الإشارة إلى الصفحة. أما إن كان حكمٌ ينطبق على عقدك فذلك سؤال للمحامي." },
  pt: { demoNotice: "Demo — não introduza dados reais de pacientes. A inferência é executada na UE (europe-west3), mas as respostas não substituem o juízo de um clínico.", legalNotice: "Não é aconselhamento jurídico. São as palavras textuais da lei, citadas à página. Se uma disposição se aplica ao seu contrato é uma questão para um advogado." },
  nl: { demoNotice: "Demo — voer geen echte patiëntgegevens in. Inferentie draait in de EU (europe-west3), maar de antwoorden vervangen het oordeel van een arts niet.", legalNotice: "Geen juridisch advies. Dit zijn de eigen woorden van de wet, met paginaverwijzing. Of een bepaling op uw contract van toepassing is, is een vraag voor een jurist." },
  ro: { demoNotice: "Demo — nu introduceți date reale ale pacienților. Inferența rulează în UE (europe-west3), dar răspunsurile nu înlocuiesc judecata unui clinician.", legalNotice: "Nu este consiliere juridică. Acestea sunt cuvintele proprii ale legii, citate la pagină. Dacă o dispoziție se aplică contractului dvs. este o întrebare pentru un avocat." },
  el: { demoNotice: "Demo — μην εισάγετε πραγματικά δεδομένα ασθενών. Η εξαγωγή συμπερασμάτων εκτελείται στην ΕΕ (europe-west3), αλλά οι απαντήσεις δεν υποκαθιστούν την κρίση του κλινικού γιατρού.", legalNotice: "Δεν αποτελεί νομική συμβουλή. Είναι τα ίδια τα λόγια του νόμου, με παραπομπή στη σελίδα. Το αν μια διάταξη ισχύει για τη σύμβασή σας είναι ερώτημα για δικηγόρο." },
  cs: { demoNotice: "Demo — nezadávejte skutečná data pacientů. Inference běží v EU (europe-west3), ale odpovědi nenahrazují úsudek lékaře.", legalNotice: "Nejde o právní poradenství. Jsou to vlastní slova zákona s odkazem na stránku. Zda se ustanovení vztahuje na vaši smlouvu, je otázka pro právníka." },
  hu: { demoNotice: "Demó — ne adjon meg valós betegadatokat. A következtetés az EU-ban fut (europe-west3), de a válaszok nem helyettesítik az orvos ítéletét.", legalNotice: "Ez nem jogi tanácsadás. Ezek a törvény saját szavai, oldalhivatkozással. Hogy egy rendelkezés vonatkozik-e a szerződésére, az ügyvédre tartozó kérdés." },
};

// The remaining strings — including the safety-critical decline messages (noAnswer.*, translation.*)
// and the interpolated function strings. These are ASSISTANT translations, not native-reviewed: the
// owner asked for a full 17-language UI knowing that, so they ship at the same "pending native
// clinical review" status as de/ka. The corpus-silent vs we-malfunctioned distinction (see the file
// header) is preserved in each: sources_do_not_answer = a fact about the corpus; verification_failed =
// a fact about the system failing. A native clinician review per language is still the right next step.
type Deep = Pick<
  Strings,
  | "legalTableNotice" | "noAnswer" | "errors" | "register" | "rejected"
  | "superseded" | "unreadable" | "damagedPageQuote" | "combined" | "pdf" | "translation"
>;
const DEEP: Record<Exclude<UiLang, "en" | "de" | "ka">, Deep> = {
  ru: {
    legalTableNotice: "Тарифные таблицы (Honorartafeln) не цитируются: заголовки столбцов не переносятся вместе со строками, поэтому цифра может быть показана под неверной тарифной зоной. Спрашивайте правило, а не сумму.",
    noAnswer: {
      no_relevant_sources: "Ни одно руководство в корпусе не охватывает этот вопрос.",
      sources_do_not_answer: "Руководства найдены, но ни один из их фрагментов не отвечает на этот вопрос.",
      verification_failed: "Ответ был получен, но его не удалось сверить с источником, поэтому он не показывается. Это сбой системы, а не отсутствие рекомендации.",
      table_not_citable: "Ответ содержится в таблице, и система пока не может процитировать его вместе с заголовками столбцов, которые придают смысл номерам категорий. Вместо того чтобы показать номер без заголовка, он не выводится — откройте страницу источника, чтобы прочитать таблицу напрямую.",
      fallback: "Нет ответа.",
      hint: "Задайте более конкретный вопрос или сформулируйте его так, как написаны эти руководства — например, какой метод контрацепции подходит при определённом состоянии, а не как лечить боль в целом.",
      incompleteSources: (s) => `Примечание: часть просмотренного не удалось прочитать. Не удалось извлечь страницы из ${s.join(", ")} — число в скобках показывает, сколько. Это не значит, что ответ был на одной из них, но источник этого ответа неполон, поэтому молчание здесь — более слабое доказательство, чем кажется.`,
    },
    errors: {
      auth: "Не удалось подтвердить вашу сессию. Войдите снова или проверьте, работает ли служба входа.",
      server: "Сервер не ответил. Возможно, он запускается или недоступен — подождите немного и попробуйте снова.",
      request: (d) => `Не удалось обработать запрос: ${d}`,
    },
    register: {
      heading: "Создайте учётную запись",
      intro: "Регистрация по приглашению. Введите код, выданный администратором вашей клиники, и он определит вашу клинику.",
      code: "Код приглашения",
      codeHint: "Обязательно. Определяет, какой клинике принадлежит ваша учётная запись.",
      email: "Эл. почта",
      password: "Пароль",
      name: "Имя (необязательно)",
      submit: "Создать учётную запись",
      submitting: "Создание…",
      success: "Учётная запись готова. Теперь вы можете войти с вашим именем пользователя и паролем.",
      errors: {
        code: "Код приглашения недействителен или истёк. Уточните его у администратора клиники.",
        username: "Это имя пользователя уже занято. Выберите другое.",
        validation: "Проверьте форму — необходимы эл. почта и код.",
        server: "Сейчас не удалось завершить регистрацию. Попробуйте ещё раз через мгновение.",
      },
    },
    rejected: (n) => `Отклонено как непроверяемые и не показано цитат: ${n}.`,
    superseded: (label) => (label ? `Это издание заменено на ${label}.` : "Это издание заменено более новым."),
    unreadable: (pages) => `Страницы ${pages} этого документа не удалось прочитать, и они не отражены выше.`,
    damagedPageQuote: "Часть этой страницы не удалось прочитать, поэтому оговорка, напечатанная рядом с этим фрагментом, может в нём отсутствовать. Откройте страницу источника, прежде чем полагаться на него.",
    combined: {
      heading: "Полный текст — слова руководства, собранные вместе",
      note: "Те же дословные цитаты выше, собранные здесь для чтения в одном месте. Это не резюме и не объединение в единое утверждение — каждая цитата отдельна и сохранена со своей страницей.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
    pdf: {
      cited: "цитируется", unreadablePage: "нечитаемая страница", prev: "‹ Назад", next: "Вперёд ›",
      close: "Закрыть", page: "Страница", of: "из", loading: "Загрузка источника…",
      error: (m) => `Не удалось загрузить PDF источника: ${m}. Запущен ли бэкенд?`,
    },
    translation: {
      label: "Перевод:",
      banner: (l) => `Машинный перевод · ${l} · не слова руководства, не проверено`,
      footnote: "Дословная цитата выше — это запись. Сверяйте с ней всё, на что опираетесь.",
      failed: "Не удалось перевести.",
      rateLimited: "Служба перевода достигла лимита запросов. Попробуйте позже — цитата выше не затронута.",
    },
  },
  uk: {
    legalTableNotice: "Тарифні таблиці (Honorartafeln) не цитуються: заголовки стовпців не переносяться разом із рядками, тому число може з'явитися під неправильною тарифною зоною. Питайте про правило, а не про суму.",
    noAnswer: {
      no_relevant_sources: "Жодна настанова в корпусі не охоплює це питання.",
      sources_do_not_answer: "Настанови знайдено, але жоден їхній фрагмент не відповідає на це питання.",
      verification_failed: "Відповідь було отримано, але її не вдалося звірити з джерелом, тому вона не показується. Це збій системи, а не відсутність настанови.",
      table_not_citable: "Відповідь міститься в таблиці, і система поки не може процитувати її разом із заголовками стовпців, які надають сенс номерам категорій. Замість того щоб показати номер без заголовка, він не виводиться — відкрийте сторінку джерела, щоб прочитати таблицю напряму.",
      fallback: "Немає відповіді.",
      hint: "Поставте конкретніше питання або сформулюйте його так, як написані ці настанови — наприклад, який метод контрацепції підходить за певного стану, а не як лікувати біль загалом.",
      incompleteSources: (s) => `Примітка: частину переглянутого не вдалося прочитати. Не вдалося витягти сторінки з ${s.join(", ")} — число в дужках показує, скільки. Це не означає, що відповідь була на одній із них, але джерело цієї відповіді неповне, тож мовчання тут — слабший доказ, ніж здається.`,
    },
    errors: {
      auth: "Не вдалося підтвердити вашу сесію. Увійдіть знову або перевірте, чи працює служба входу.",
      server: "Сервер не відповів. Можливо, він запускається або недоступний — зачекайте трохи й спробуйте знову.",
      request: (d) => `Не вдалося обробити запит: ${d}`,
    },
    register: {
      heading: "Створіть обліковий запис",
      intro: "Реєстрація за запрошенням. Введіть код, виданий адміністратором вашої клініки, і він визначить вашу клініку.",
      code: "Код запрошення",
      codeHint: "Обов'язково. Визначає, якій клініці належить ваш обліковий запис.",
      email: "Ел. пошта",
      password: "Пароль",
      name: "Ім'я (необов'язково)",
      submit: "Створити обліковий запис",
      submitting: "Створення…",
      success: "Обліковий запис готовий. Тепер ви можете увійти зі своїм іменем користувача та паролем.",
      errors: {
        code: "Код запрошення недійсний або прострочений. Уточніть його в адміністратора клініки.",
        username: "Це ім'я користувача вже зайняте. Виберіть інше.",
        validation: "Перевірте форму — потрібні ел. пошта та код.",
        server: "Зараз не вдалося завершити реєстрацію. Спробуйте ще раз за мить.",
      },
    },
    rejected: (n) => `Відхилено як неперевірні й не показано цитат: ${n}.`,
    superseded: (label) => (label ? `Це видання замінено на ${label}.` : "Це видання замінено новішим."),
    unreadable: (pages) => `Сторінки ${pages} цього документа не вдалося прочитати, і вони не відображені вище.`,
    damagedPageQuote: "Частину цієї сторінки не вдалося прочитати, тому застереження, надруковане поряд із цим фрагментом, може в ньому бути відсутнім. Відкрийте сторінку джерела, перш ніж покладатися на нього.",
    combined: {
      heading: "Повний текст — слова настанови, зібрані разом",
      note: "Ті самі дослівні цитати вище, зібрані тут для читання в одному місці. Це не резюме й не об'єднання в єдине твердження — кожна цитата окрема й збережена зі своєю сторінкою.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
    pdf: {
      cited: "цитується", unreadablePage: "нечитабельна сторінка", prev: "‹ Назад", next: "Далі ›",
      close: "Закрити", page: "Сторінка", of: "з", loading: "Завантаження джерела…",
      error: (m) => `Не вдалося завантажити PDF джерела: ${m}. Чи запущено бекенд?`,
    },
    translation: {
      label: "Переклад:",
      banner: (l) => `Машинний переклад · ${l} · не слова настанови, не перевірено`,
      footnote: "Дослівна цитата вище — це запис. Звіряйте з нею все, на що спираєтеся.",
      failed: "Не вдалося перекласти.",
      rateLimited: "Служба перекладу досягла ліміту запитів. Спробуйте пізніше — цитата вище не зачеплена.",
    },
  },
  fr: {
    legalTableNotice: "Les tableaux d'honoraires (Honorartafeln) ne sont pas cités : les en-têtes de colonnes ne sont pas repris avec les lignes, si bien qu'un montant pourrait apparaître sous la mauvaise zone d'honoraires. Demandez la règle plutôt que le montant.",
    noAnswer: {
      no_relevant_sources: "Aucune recommandation du corpus ne traite cette question.",
      sources_do_not_answer: "Des recommandations ont été trouvées, mais aucun de leurs passages ne répond à cette question.",
      verification_failed: "Une réponse a été produite mais n'a pas pu être vérifiée par rapport à sa source, elle est donc retenue. C'est une défaillance du système, non une absence de recommandation.",
      table_not_citable: "La réponse se trouve dans un tableau, et ce système ne peut pas encore la citer avec les en-têtes de colonnes qui donnent leur sens aux numéros de catégorie. Plutôt que d'afficher un numéro sans son en-tête, elle est retenue — ouvrez la page source pour lire le tableau directement.",
      fallback: "Aucune réponse.",
      hint: "Posez une question plus précise, ou formulez-la comme ces recommandations sont rédigées — par exemple quelle méthode contraceptive convient à une affection donnée, plutôt que comment traiter la douleur en général.",
      incompleteSources: (s) => `Remarque : une partie de ce qui a été recherché n'a pas pu être lue. Des pages n'ont pas pu être extraites de ${s.join(", ")} — le nombre entre parenthèses en indique la quantité. Cela ne signifie pas que la réponse s'y trouvait, mais la source de cette réponse est incomplète, donc un silence ici est une preuve plus faible qu'il n'y paraît.`,
    },
    errors: {
      auth: "Votre session n'a pas pu être authentifiée. Reconnectez-vous, ou vérifiez que le service de connexion fonctionne.",
      server: "Le serveur n'a pas répondu. Il démarre peut-être ou est hors service — patientez un instant et réessayez.",
      request: (d) => `La requête n'a pas pu être traitée : ${d}`,
    },
    register: {
      heading: "Créez votre compte",
      intro: "L'inscription se fait sur invitation. Saisissez le code que votre administrateur de clinique vous a donné ; il vous rattachera à votre clinique.",
      code: "Code d'invitation",
      codeHint: "Requis. Il détermine à quelle clinique appartient votre compte.",
      email: "E-mail",
      password: "Mot de passe",
      name: "Nom (facultatif)",
      submit: "Créer le compte",
      submitting: "Création…",
      success: "Votre compte est prêt. Vous pouvez maintenant vous connecter avec votre identifiant et votre mot de passe.",
      errors: {
        code: "Ce code d'invitation est invalide ou a expiré. Vérifiez-le auprès de votre administrateur de clinique.",
        username: "Cet identifiant est déjà pris. Choisissez-en un autre.",
        validation: "Veuillez vérifier le formulaire — un e-mail et un code sont requis.",
        server: "L'inscription n'a pas pu aboutir pour le moment. Veuillez réessayer dans un instant.",
      },
    },
    rejected: (n) => `${n} citation${n === 1 ? "" : "s"} rejetée${n === 1 ? "" : "s"} comme invérifiable${n === 1 ? "" : "s"} et non affichée${n === 1 ? "" : "s"}.`,
    superseded: (label) => (label ? `Cette édition a été remplacée par ${label}.` : "Cette édition a été remplacée."),
    unreadable: (pages) => `Les pages ${pages} de ce document n'ont pas pu être lues et ne sont pas reflétées ci-dessus.`,
    damagedPageQuote: "Une partie de cette page n'a pas pu être lue ; une réserve imprimée à côté de ce passage peut donc y manquer. Ouvrez la page source avant de vous y fier.",
    combined: {
      heading: "Texte intégral — les mots de la recommandation, rassemblés",
      note: "Les mêmes citations textuelles ci-dessus, réunies ici pour être lues en un seul endroit. Ni un résumé ni une fusion en un énoncé unique — chacune est un extrait distinct, conservé avec sa page.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
    pdf: {
      cited: "cité", unreadablePage: "page illisible", prev: "‹ Préc.", next: "Suiv. ›",
      close: "Fermer", page: "Page", of: "sur", loading: "Chargement de la source…",
      error: (m) => `Impossible de charger le PDF source : ${m}. Le backend est-il en cours d'exécution ?`,
    },
    translation: {
      label: "Traduire :",
      banner: (l) => `Traduction automatique · ${l} · pas les mots de la recommandation, non vérifiée`,
      footnote: "La citation textuelle ci-dessus fait foi. Vérifiez-y tout ce sur quoi vous agissez.",
      failed: "Échec de la traduction.",
      rateLimited: "Le service de traduction a atteint sa limite de requêtes. Réessayez plus tard — la citation ci-dessus n'est pas affectée.",
    },
  },
  es: {
    legalTableNotice: "Las tablas de honorarios (Honorartafeln) no se citan: los encabezados de columna no se conservan con las filas, por lo que una cifra podría mostrarse bajo la zona de honorarios equivocada. Pregunte por la regla, no por el importe.",
    noAnswer: {
      no_relevant_sources: "Ninguna guía del corpus cubre esta pregunta.",
      sources_do_not_answer: "Se encontraron guías, pero ninguno de sus pasajes responde a esta pregunta.",
      verification_failed: "Se produjo una respuesta pero no pudo verificarse frente a su fuente, por lo que se retiene. Es un fallo del sistema, no la ausencia de una guía.",
      table_not_citable: "La respuesta está en una tabla, y este sistema aún no puede citarla con los encabezados de columna que dan sentido a sus números de categoría. En lugar de mostrar un número sin su encabezado, se retiene — abra la página de origen para leer la tabla directamente.",
      fallback: "Sin respuesta.",
      hint: "Formule una pregunta más específica, o plantéela como están redactadas estas guías — por ejemplo, qué método anticonceptivo conviene a una determinada afección, en lugar de cómo tratar el dolor en general.",
      incompleteSources: (s) => `Nota: parte de lo consultado no pudo leerse. No se pudieron extraer páginas de ${s.join(", ")} — el número entre paréntesis indica cuántas. Esto no significa que la respuesta estuviera en una de ellas, pero la fuente de esta respuesta está incompleta, así que un silencio aquí es una evidencia más débil de lo que parece.`,
    },
    errors: {
      auth: "No se pudo autenticar su sesión. Vuelva a iniciar sesión, o compruebe que el servicio de acceso está funcionando.",
      server: "El servidor no respondió. Puede estar iniciándose o caído — espere un momento e inténtelo de nuevo.",
      request: (d) => `No se pudo procesar la solicitud: ${d}`,
    },
    register: {
      heading: "Cree su cuenta",
      intro: "El registro es por invitación. Introduzca el código que le dio el administrador de su clínica; le asignará a su clínica.",
      code: "Código de invitación",
      codeHint: "Obligatorio. Decide a qué clínica pertenece su cuenta.",
      email: "Correo electrónico",
      password: "Contraseña",
      name: "Nombre (opcional)",
      submit: "Crear cuenta",
      submitting: "Creando…",
      success: "Su cuenta está lista. Ya puede iniciar sesión con su nombre de usuario y contraseña.",
      errors: {
        code: "Ese código de invitación no es válido o ha caducado. Compruébelo con el administrador de su clínica.",
        username: "Ese nombre de usuario ya está en uso. Elija otro.",
        validation: "Revise el formulario — se requieren un correo electrónico y un código.",
        server: "El registro no pudo completarse en este momento. Inténtelo de nuevo en un instante.",
      },
    },
    rejected: (n) => `${n} cita${n === 1 ? "" : "s"} rechazada${n === 1 ? "" : "s"} como no verificable${n === 1 ? "" : "s"} y no se muestra${n === 1 ? "" : "n"}.`,
    superseded: (label) => (label ? `Esta edición ha sido reemplazada por ${label}.` : "Esta edición ha sido reemplazada."),
    unreadable: (pages) => `Las páginas ${pages} de este documento no pudieron leerse y no se reflejan arriba.`,
    damagedPageQuote: "Parte de esta página no pudo leerse, por lo que una salvedad impresa junto a este pasaje podría faltar en él. Abra la página de origen antes de basarse en ella.",
    combined: {
      heading: "Texto completo — las palabras de la guía, reunidas",
      note: "Las mismas citas textuales de arriba, reunidas aquí para leerlas en un solo lugar. No es un resumen ni una fusión en una sola afirmación — cada una es un fragmento distinto, conservado con su página.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
    pdf: {
      cited: "citado", unreadablePage: "página ilegible", prev: "‹ Ant.", next: "Sig. ›",
      close: "Cerrar", page: "Página", of: "de", loading: "Cargando la fuente…",
      error: (m) => `No se pudo cargar el PDF de origen: ${m}. ¿Está el backend en funcionamiento?`,
    },
    translation: {
      label: "Traducir:",
      banner: (l) => `Traducción automática · ${l} · no son las palabras de la guía, sin verificar`,
      footnote: "La cita textual de arriba es el registro. Contraste con ella todo aquello sobre lo que actúe.",
      failed: "La traducción falló.",
      rateLimited: "El servicio de traducción ha alcanzado su límite de solicitudes. Inténtelo más tarde — la cita de arriba no se ve afectada.",
    },
  },
  it: {
    legalTableNotice: "Le tabelle degli onorari (Honorartafeln) non vengono citate: le intestazioni di colonna non sono riportate con le righe, quindi un importo potrebbe comparire sotto la zona di onorario sbagliata. Chieda la regola anziché l'importo.",
    noAnswer: {
      no_relevant_sources: "Nessuna linea guida del corpus copre questa domanda.",
      sources_do_not_answer: "Sono state trovate linee guida, ma nessuno dei loro passaggi risponde a questa domanda.",
      verification_failed: "È stata prodotta una risposta ma non è stato possibile verificarla rispetto alla sua fonte, perciò viene trattenuta. È un guasto del sistema, non l'assenza di una linea guida.",
      table_not_citable: "La risposta è in una tabella, e questo sistema non può ancora citarla con le intestazioni di colonna che danno significato ai suoi numeri di categoria. Anziché mostrare un numero senza la sua intestazione, viene trattenuta — apra la pagina di origine per leggere la tabella direttamente.",
      fallback: "Nessuna risposta.",
      hint: "Ponga una domanda più specifica, o la formuli come sono scritte queste linee guida — per esempio quale metodo contraccettivo è adatto a una determinata condizione, anziché come trattare il dolore in generale.",
      incompleteSources: (s) => `Nota: parte di ciò che è stato cercato non è stato possibile leggerlo. Non è stato possibile estrarre pagine da ${s.join(", ")} — il numero tra parentesi indica quante. Ciò non significa che la risposta fosse in una di esse, ma la fonte di questa risposta è incompleta, quindi un silenzio qui è una prova più debole di quanto sembri.`,
    },
    errors: {
      auth: "Non è stato possibile autenticare la sua sessione. Acceda di nuovo, o verifichi che il servizio di accesso sia in funzione.",
      server: "Il server non ha risposto. Potrebbe essere in avvio o non disponibile — attenda un momento e riprovi.",
      request: (d) => `Non è stato possibile elaborare la richiesta: ${d}`,
    },
    register: {
      heading: "Crei il suo account",
      intro: "La registrazione è su invito. Inserisca il codice fornito dall'amministratore della sua clinica; la assegnerà alla sua clinica.",
      code: "Codice di invito",
      codeHint: "Obbligatorio. Decide a quale clinica appartiene il suo account.",
      email: "E-mail",
      password: "Password",
      name: "Nome (facoltativo)",
      submit: "Crea account",
      submitting: "Creazione…",
      success: "Il suo account è pronto. Ora può accedere con il suo nome utente e la sua password.",
      errors: {
        code: "Questo codice di invito non è valido o è scaduto. Lo verifichi con l'amministratore della sua clinica.",
        username: "Questo nome utente è già in uso. Ne scelga un altro.",
        validation: "Controlli il modulo — sono richiesti un'e-mail e un codice.",
        server: "Non è stato possibile completare la registrazione in questo momento. Riprovi tra un istante.",
      },
    },
    rejected: (n) => `${n} citazione${n === 1 ? "" : "i"} respinta${n === 1 ? "" : "e"} come non verificabile${n === 1 ? "" : "i"} e non mostrata${n === 1 ? "" : "e"}.`,
    superseded: (label) => (label ? `Questa edizione è stata sostituita da ${label}.` : "Questa edizione è stata sostituita."),
    unreadable: (pages) => `Le pagine ${pages} di questo documento non è stato possibile leggerle e non sono riflesse sopra.`,
    damagedPageQuote: "Parte di questa pagina non è stato possibile leggerla, quindi una precisazione stampata accanto a questo passaggio potrebbe mancarvi. Apra la pagina di origine prima di farvi affidamento.",
    combined: {
      heading: "Testo integrale — le parole della linea guida, raccolte",
      note: "Le stesse citazioni testuali qui sopra, riunite qui per leggerle in un unico luogo. Non un riassunto né una fusione in un'unica affermazione — ciascuna è un estratto distinto, conservato con la sua pagina.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
    pdf: {
      cited: "citato", unreadablePage: "pagina illeggibile", prev: "‹ Prec.", next: "Succ. ›",
      close: "Chiudi", page: "Pagina", of: "di", loading: "Caricamento della fonte…",
      error: (m) => `Impossibile caricare il PDF di origine: ${m}. Il backend è in esecuzione?`,
    },
    translation: {
      label: "Traduci:",
      banner: (l) => `Traduzione automatica · ${l} · non sono le parole della linea guida, non verificata`,
      footnote: "La citazione testuale qui sopra fa fede. Verifichi rispetto ad essa tutto ciò su cui agisce.",
      failed: "Traduzione non riuscita.",
      rateLimited: "Il servizio di traduzione ha raggiunto il suo limite di richieste. Riprovi più tardi — la citazione qui sopra non è interessata.",
    },
  },
  pl: {
    legalTableNotice: "Tabele honorariów (Honorartafeln) nie są cytowane: nagłówki kolumn nie są przenoszone wraz z wierszami, więc kwota mogłaby zostać pokazana pod niewłaściwą strefą honorarium. Pytaj o regułę, a nie o kwotę.",
    noAnswer: {
      no_relevant_sources: "Żadna wytyczna w korpusie nie obejmuje tego pytania.",
      sources_do_not_answer: "Znaleziono wytyczne, ale żaden z ich fragmentów nie odpowiada na to pytanie.",
      verification_failed: "Odpowiedź została wygenerowana, ale nie udało się jej zweryfikować względem źródła, dlatego zostaje wstrzymana. To błąd systemu, a nie brak wytycznej.",
      table_not_citable: "Odpowiedź znajduje się w tabeli, a system nie potrafi jeszcze zacytować jej wraz z nagłówkami kolumn, które nadają sens numerom kategorii. Zamiast pokazywać numer bez nagłówka, jest wstrzymana — otwórz stronę źródłową, aby przeczytać tabelę bezpośrednio.",
      fallback: "Brak odpowiedzi.",
      hint: "Zadaj bardziej konkretne pytanie lub sformułuj je tak, jak napisane są te wytyczne — na przykład która metoda antykoncepcji pasuje do danego schorzenia, zamiast jak leczyć ból w ogóle.",
      incompleteSources: (s) => `Uwaga: części przeszukanego materiału nie udało się odczytać. Nie udało się wyodrębnić stron z ${s.join(", ")} — liczba w nawiasie mówi ile. Nie oznacza to, że odpowiedź była na jednej z nich, ale źródło tej odpowiedzi jest niekompletne, więc milczenie tutaj jest słabszym dowodem, niż się wydaje.`,
    },
    errors: {
      auth: "Nie udało się uwierzytelnić Twojej sesji. Zaloguj się ponownie lub sprawdź, czy usługa logowania działa.",
      server: "Serwer nie odpowiedział. Może się uruchamiać lub być niedostępny — poczekaj chwilę i spróbuj ponownie.",
      request: (d) => `Nie udało się przetworzyć żądania: ${d}`,
    },
    register: {
      heading: "Utwórz konto",
      intro: "Rejestracja odbywa się na zaproszenie. Wpisz kod otrzymany od administratora Twojej kliniki; przypisze Cię on do Twojej kliniki.",
      code: "Kod zaproszenia",
      codeHint: "Wymagany. Decyduje, do której kliniki należy Twoje konto.",
      email: "E-mail",
      password: "Hasło",
      name: "Imię (opcjonalnie)",
      submit: "Utwórz konto",
      submitting: "Tworzenie…",
      success: "Twoje konto jest gotowe. Możesz teraz zalogować się swoją nazwą użytkownika i hasłem.",
      errors: {
        code: "Ten kod zaproszenia jest nieprawidłowy lub wygasł. Sprawdź go u administratora swojej kliniki.",
        username: "Ta nazwa użytkownika jest już zajęta. Wybierz inną.",
        validation: "Sprawdź formularz — wymagane są e-mail i kod.",
        server: "Rejestracji nie udało się teraz ukończyć. Spróbuj ponownie za chwilę.",
      },
    },
    rejected: (n) => `Odrzucono jako niemożliwe do zweryfikowania i nie pokazano cytatów: ${n}.`,
    superseded: (label) => (label ? `To wydanie zostało zastąpione przez ${label}.` : "To wydanie zostało zastąpione."),
    unreadable: (pages) => `Stron ${pages} tego dokumentu nie udało się odczytać i nie są one uwzględnione powyżej.`,
    damagedPageQuote: "Części tej strony nie udało się odczytać, więc zastrzeżenie wydrukowane obok tego fragmentu może w nim brakować. Otwórz stronę źródłową, zanim się na nim oprzesz.",
    combined: {
      heading: "Pełny tekst — słowa wytycznej, zebrane razem",
      note: "Te same dosłowne cytaty powyżej, zebrane tutaj do przeczytania w jednym miejscu. Nie streszczenie i nie połączenie w jedno stwierdzenie — każdy jest osobnym fragmentem, zachowanym ze swoją stroną.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
    pdf: {
      cited: "cytowane", unreadablePage: "strona nieczytelna", prev: "‹ Poprz.", next: "Nast. ›",
      close: "Zamknij", page: "Strona", of: "z", loading: "Ładowanie źródła…",
      error: (m) => `Nie udało się załadować źródłowego PDF: ${m}. Czy backend działa?`,
    },
    translation: {
      label: "Przetłumacz:",
      banner: (l) => `Tłumaczenie maszynowe · ${l} · to nie słowa wytycznej, niezweryfikowane`,
      footnote: "Dosłowny cytat powyżej jest zapisem. Sprawdzaj względem niego wszystko, na czym się opierasz.",
      failed: "Tłumaczenie nie powiodło się.",
      rateLimited: "Usługa tłumaczenia osiągnęła limit żądań. Spróbuj później — cytat powyżej pozostaje bez zmian.",
    },
  },
  tr: {
    legalTableNotice: "Ücret tabloları (Honorartafeln) alıntılanmaz: sütun başlıkları satırlarla birlikte taşınmaz, bu yüzden bir tutar yanlış ücret bölgesi altında gösterilebilir. Tutarı değil, kuralı sorun.",
    noAnswer: {
      no_relevant_sources: "Külliyattaki hiçbir kılavuz bu soruyu kapsamıyor.",
      sources_do_not_answer: "Kılavuzlar bulundu, ancak hiçbir bölümü bu soruyu yanıtlamıyor.",
      verification_failed: "Bir yanıt üretildi ancak kaynağına karşı doğrulanamadı, bu nedenle gösterilmiyor. Bu, bir kılavuzun eksikliği değil, bir sistem arızasıdır.",
      table_not_citable: "Yanıt bir tabloda ve bu sistem henüz onu, kategori numaralarına anlam veren sütun başlıklarıyla birlikte alıntılayamıyor. Bir sayıyı başlığı olmadan göstermek yerine gösterilmiyor — tabloyu doğrudan okumak için kaynak sayfayı açın.",
      fallback: "Yanıt yok.",
      hint: "Daha belirli bir soru sorun ya da bu kılavuzların yazıldığı biçimde ifade edin — örneğin ağrının genel olarak nasıl tedavi edileceği yerine, belirli bir duruma hangi doğum kontrol yönteminin uygun olduğu gibi.",
      incompleteSources: (s) => `Not: aranan içeriğin bir kısmı okunamadı. ${s.join(", ")} kaynağından sayfalar çıkarılamadı — parantez içindeki sayı kaç tane olduğunu gösterir. Bu, yanıtın bunlardan birinde olduğu anlamına gelmez, ancak bu yanıtın arkasındaki kaynak eksik olduğundan buradaki sessizlik göründüğünden daha zayıf bir kanıttır.`,
    },
    errors: {
      auth: "Oturumunuz doğrulanamadı. Yeniden oturum açın ya da giriş hizmetinin çalıştığını kontrol edin.",
      server: "Sunucu yanıt vermedi. Başlıyor ya da kapalı olabilir — bir an bekleyip yeniden deneyin.",
      request: (d) => `İstek işlenemedi: ${d}`,
    },
    register: {
      heading: "Hesabınızı oluşturun",
      intro: "Kayıt davetiyeledir. Klinik yöneticinizin verdiği kodu girin; sizi kliniğinize yerleştirecektir.",
      code: "Davet kodu",
      codeHint: "Gerekli. Hesabınızın hangi kliniğe ait olduğunu belirler.",
      email: "E-posta",
      password: "Parola",
      name: "Ad (isteğe bağlı)",
      submit: "Hesap oluştur",
      submitting: "Oluşturuluyor…",
      success: "Hesabınız hazır. Artık kullanıcı adınız ve parolanızla oturum açabilirsiniz.",
      errors: {
        code: "Bu davet kodu geçersiz ya da süresi dolmuş. Klinik yöneticinizle kontrol edin.",
        username: "Bu kullanıcı adı zaten alınmış. Başka bir tane seçin.",
        validation: "Lütfen formu kontrol edin — bir e-posta ve bir kod gerekli.",
        server: "Kayıt şu anda tamamlanamadı. Lütfen bir an sonra yeniden deneyin.",
      },
    },
    rejected: (n) => `${n} alıntı doğrulanamaz olarak reddedildi ve gösterilmiyor.`,
    superseded: (label) => (label ? `Bu baskı ${label} ile değiştirildi.` : "Bu baskı daha yenisiyle değiştirildi."),
    unreadable: (pages) => `Bu belgenin ${pages} sayfaları okunamadı ve yukarıya yansıtılmadı.`,
    damagedPageQuote: "Bu sayfanın bir kısmı okunamadı, bu nedenle bu bölümün yanında basılı bir çekince ondan eksik olabilir. Ona güvenmeden önce kaynak sayfayı açın.",
    combined: {
      heading: "Tam metin — kılavuzun kendi sözleri, bir araya getirilmiş",
      note: "Yukarıdaki aynı birebir alıntılar, tek bir yerde okumak için burada toplandı. Bir özet değil ve tek bir ifadeye birleştirilmemiş — her biri ayrı bir bölüm, kendi sayfasıyla saklanmış.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
    pdf: {
      cited: "alıntılanan", unreadablePage: "okunamayan sayfa", prev: "‹ Önceki", next: "Sonraki ›",
      close: "Kapat", page: "Sayfa", of: "/", loading: "Kaynak yükleniyor…",
      error: (m) => `Kaynak PDF yüklenemedi: ${m}. Arka uç çalışıyor mu?`,
    },
    translation: {
      label: "Çevir:",
      banner: (l) => `Makine çevirisi · ${l} · kılavuzun sözleri değil, doğrulanmadı`,
      footnote: "Yukarıdaki birebir alıntı esas kayıttır. Üzerine hareket edeceğiniz her şeyi ona karşı kontrol edin.",
      failed: "Çeviri başarısız oldu.",
      rateLimited: "Çeviri hizmeti istek sınırına ulaştı. Daha sonra yeniden deneyin — yukarıdaki alıntı etkilenmez.",
    },
  },
  ar: {
    legalTableNotice: "جداول الأتعاب (Honorartafeln) لا تُقتبس: عناوين الأعمدة لا تُنقل مع الصفوف، لذا قد يظهر مبلغ تحت منطقة أتعاب خاطئة. اسأل عن القاعدة لا عن المبلغ.",
    noAnswer: {
      no_relevant_sources: "لا يوجد في المجموعة أي دليل يتناول هذا السؤال.",
      sources_do_not_answer: "عُثر على أدلة، لكن لا يوجد في مقاطعها ما يجيب على هذا السؤال.",
      verification_failed: "أُنتجت إجابة لكن تعذّر التحقق منها مقابل مصدرها، لذا يُمتنع عن عرضها. هذا خلل في النظام، وليس غياباً للإرشاد.",
      table_not_citable: "الإجابة في جدول، ولا يستطيع هذا النظام بعدُ اقتباسها مع عناوين الأعمدة التي تمنح أرقام الفئات معناها. وبدلاً من عرض رقم دون عنوانه، يُمتنع عنها — افتح صفحة المصدر لقراءة الجدول مباشرة.",
      fallback: "لا إجابة.",
      hint: "اطرح سؤالاً أكثر تحديداً، أو صُغه بالطريقة التي كُتبت بها هذه الأدلة — مثلاً أيّ وسيلة لمنع الحمل تناسب حالة بعينها، بدلاً من كيفية علاج الألم بوجه عام.",
      incompleteSources: (s) => `ملاحظة: جزء مما جرى البحث فيه تعذّرت قراءته. تعذّر استخراج صفحات من ${s.join("، ")} — والعدد بين قوسين يبيّن كم. لا يعني هذا أن الإجابة كانت في إحداها، لكن المصدر وراء هذا الرد ناقص، فالصمت هنا دليل أضعف مما يبدو.`,
    },
    errors: {
      auth: "تعذّرت مصادقة جلستك. سجّل الدخول من جديد، أو تحقّق من أن خدمة تسجيل الدخول تعمل.",
      server: "لم يستجب الخادم. قد يكون قيد التشغيل أو متوقفاً — انتظر لحظة وحاول مجدداً.",
      request: (d) => `تعذّرت معالجة الطلب: ${d}`,
    },
    register: {
      heading: "أنشئ حسابك",
      intro: "التسجيل بالدعوة. أدخل الرمز الذي أعطاك إياه مسؤول عيادتك، وسيُلحقك بعيادتك.",
      code: "رمز الدعوة",
      codeHint: "مطلوب. يحدّد العيادة التي ينتمي إليها حسابك.",
      email: "البريد الإلكتروني",
      password: "كلمة المرور",
      name: "الاسم (اختياري)",
      submit: "إنشاء الحساب",
      submitting: "جارٍ الإنشاء…",
      success: "حسابك جاهز. يمكنك الآن تسجيل الدخول باسم المستخدم وكلمة المرور.",
      errors: {
        code: "رمز الدعوة هذا غير صالح أو منتهي الصلاحية. تحقّق منه مع مسؤول عيادتك.",
        username: "اسم المستخدم هذا مأخوذ بالفعل. اختر غيره.",
        validation: "يرجى التحقق من النموذج — البريد الإلكتروني والرمز مطلوبان.",
        server: "تعذّر إكمال التسجيل الآن. يرجى المحاولة مجدداً بعد لحظة.",
      },
    },
    rejected: (n) => `رُفض ${n} اقتباساً لتعذّر التحقق منه ولا يُعرض.`,
    superseded: (label) => (label ? `حلّت محلّ هذه الطبعة ${label}.` : "حلّت محلّ هذه الطبعة طبعة أحدث."),
    unreadable: (pages) => `تعذّرت قراءة الصفحات ${pages} من هذا المستند وهي غير ممثّلة أعلاه.`,
    damagedPageQuote: "تعذّرت قراءة جزء من هذه الصفحة، لذا قد يكون قيدٌ مطبوع بجوار هذا المقطع غائباً عنه. افتح صفحة المصدر قبل الاعتماد عليه.",
    combined: {
      heading: "النص الكامل — كلمات الدليل نفسها، مجموعة",
      note: "الاقتباسات الحرفية ذاتها أعلاه، جُمعت هنا لقراءتها في مكان واحد. ليست ملخّصاً ولا دمجاً في عبارة واحدة — كل منها مقطع مستقل، محفوظ مع صفحته.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
    pdf: {
      cited: "مقتبس", unreadablePage: "صفحة غير مقروءة", prev: "‹ السابق", next: "التالي ›",
      close: "إغلاق", page: "صفحة", of: "من", loading: "جارٍ تحميل المصدر…",
      error: (m) => `تعذّر تحميل ملف PDF المصدر: ${m}. هل الخادم الخلفي يعمل؟`,
    },
    translation: {
      label: "ترجمة:",
      banner: (l) => `ترجمة آلية · ${l} · ليست كلمات الدليل، وغير مُتحقَّق منها`,
      footnote: "الاقتباس الحرفي أعلاه هو السجل. راجع مقابله كل ما تبني عليه عملاً.",
      failed: "فشلت الترجمة.",
      rateLimited: "بلغت خدمة الترجمة حدّ طلباتها. حاول لاحقاً — الاقتباس أعلاه غير متأثر.",
    },
  },
  pt: {
    legalTableNotice: "As tabelas de honorários (Honorartafeln) não são citadas: os cabeçalhos das colunas não são levados com as linhas, pelo que um valor poderia aparecer sob a zona de honorários errada. Pergunte pela regra, não pelo valor.",
    noAnswer: {
      no_relevant_sources: "Nenhuma diretriz do corpus cobre esta questão.",
      sources_do_not_answer: "Foram encontradas diretrizes, mas nenhuma das suas passagens responde a esta questão.",
      verification_failed: "Foi produzida uma resposta mas não foi possível verificá-la face à sua fonte, pelo que é retida. É uma falha do sistema, não a ausência de uma diretriz.",
      table_not_citable: "A resposta está numa tabela, e este sistema ainda não consegue citá-la com os cabeçalhos das colunas que dão sentido aos seus números de categoria. Em vez de mostrar um número sem o seu cabeçalho, é retida — abra a página de origem para ler a tabela diretamente.",
      fallback: "Sem resposta.",
      hint: "Faça uma pergunta mais específica, ou formule-a como estas diretrizes estão escritas — por exemplo, que método contracetivo é adequado a uma determinada condição, em vez de como tratar a dor em geral.",
      incompleteSources: (s) => `Nota: parte do que foi pesquisado não pôde ser lido. Não foi possível extrair páginas de ${s.join(", ")} — o número entre parênteses indica quantas. Isto não significa que a resposta estivesse numa delas, mas a fonte por trás desta resposta está incompleta, pelo que um silêncio aqui é uma prova mais fraca do que parece.`,
    },
    errors: {
      auth: "Não foi possível autenticar a sua sessão. Inicie sessão novamente, ou verifique se o serviço de autenticação está a funcionar.",
      server: "O servidor não respondeu. Pode estar a iniciar ou em baixo — aguarde um momento e tente novamente.",
      request: (d) => `Não foi possível processar o pedido: ${d}`,
    },
    register: {
      heading: "Crie a sua conta",
      intro: "O registo é por convite. Introduza o código que o administrador da sua clínica lhe deu; ele irá associá-lo à sua clínica.",
      code: "Código de convite",
      codeHint: "Obrigatório. Decide a que clínica pertence a sua conta.",
      email: "E-mail",
      password: "Palavra-passe",
      name: "Nome (opcional)",
      submit: "Criar conta",
      submitting: "A criar…",
      success: "A sua conta está pronta. Já pode iniciar sessão com o seu nome de utilizador e palavra-passe.",
      errors: {
        code: "Esse código de convite é inválido ou expirou. Verifique-o com o administrador da sua clínica.",
        username: "Esse nome de utilizador já está em uso. Escolha outro.",
        validation: "Verifique o formulário — são necessários um e-mail e um código.",
        server: "Não foi possível concluir o registo neste momento. Tente novamente dentro de instantes.",
      },
    },
    rejected: (n) => `${n} citação${n === 1 ? "" : "ões"} rejeitada${n === 1 ? "" : "s"} como não verificável${n === 1 ? "" : "eis"} e não é${n === 1 ? "" : "são"} mostrada${n === 1 ? "" : "s"}.`,
    superseded: (label) => (label ? `Esta edição foi substituída por ${label}.` : "Esta edição foi substituída."),
    unreadable: (pages) => `As páginas ${pages} deste documento não puderam ser lidas e não se refletem acima.`,
    damagedPageQuote: "Parte desta página não pôde ser lida, pelo que uma ressalva impressa junto a esta passagem pode estar em falta nela. Abra a página de origem antes de confiar nela.",
    combined: {
      heading: "Texto integral — as palavras da diretriz, reunidas",
      note: "As mesmas citações textuais acima, reunidas aqui para ler num só lugar. Não é um resumo nem uma fusão numa única afirmação — cada uma é um excerto distinto, conservado com a sua página.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
    pdf: {
      cited: "citado", unreadablePage: "página ilegível", prev: "‹ Ant.", next: "Seg. ›",
      close: "Fechar", page: "Página", of: "de", loading: "A carregar a fonte…",
      error: (m) => `Não foi possível carregar o PDF de origem: ${m}. O backend está a funcionar?`,
    },
    translation: {
      label: "Traduzir:",
      banner: (l) => `Tradução automática · ${l} · não são as palavras da diretriz, não verificada`,
      footnote: "A citação textual acima é o registo. Confirme com ela tudo aquilo sobre o que agir.",
      failed: "A tradução falhou.",
      rateLimited: "O serviço de tradução atingiu o seu limite de pedidos. Tente mais tarde — a citação acima não é afetada.",
    },
  },
  nl: {
    legalTableNotice: "De honorariumtabellen (Honorartafeln) worden niet geciteerd: de kolomkoppen worden niet met de rijen meegenomen, zodat een bedrag onder de verkeerde honorariumzone kan verschijnen. Vraag naar de regel, niet naar het bedrag.",
    noAnswer: {
      no_relevant_sources: "Geen enkele richtlijn in het corpus behandelt deze vraag.",
      sources_do_not_answer: "Er zijn richtlijnen gevonden, maar geen van hun passages beantwoordt deze vraag.",
      verification_failed: "Er is een antwoord geproduceerd, maar het kon niet tegen zijn bron worden geverifieerd en wordt daarom achtergehouden. Dit is een systeemfout, niet het ontbreken van een richtlijn.",
      table_not_citable: "Het antwoord staat in een tabel, en dit systeem kan het nog niet citeren met de kolomkoppen die de categorienummers betekenis geven. In plaats van een nummer zonder kop te tonen, wordt het achtergehouden — open de bronpagina om de tabel rechtstreeks te lezen.",
      fallback: "Geen antwoord.",
      hint: "Stel een specifiekere vraag, of formuleer haar zoals deze richtlijnen zijn geschreven — bijvoorbeeld welke anticonceptiemethode past bij een bepaalde aandoening, in plaats van hoe pijn in het algemeen te behandelen.",
      incompleteSources: (s) => `Let op: een deel van wat is doorzocht, kon niet worden gelezen. Er konden geen pagina's worden geëxtraheerd uit ${s.join(", ")} — het getal tussen haakjes geeft aan hoeveel. Dit betekent niet dat het antwoord op een ervan stond, maar de bron achter dit antwoord is onvolledig, dus een stilte hier is zwakker bewijs dan het lijkt.`,
    },
    errors: {
      auth: "Uw sessie kon niet worden geverifieerd. Meld u opnieuw aan, of controleer of de aanmeldservice draait.",
      server: "De server reageerde niet. Hij start mogelijk op of is uit — wacht even en probeer opnieuw.",
      request: (d) => `Het verzoek kon niet worden verwerkt: ${d}`,
    },
    register: {
      heading: "Maak uw account aan",
      intro: "Registratie gaat op uitnodiging. Voer de code in die uw kliniekbeheerder u gaf; die plaatst u in uw kliniek.",
      code: "Uitnodigingscode",
      codeHint: "Vereist. Bepaalt tot welke kliniek uw account behoort.",
      email: "E-mail",
      password: "Wachtwoord",
      name: "Naam (optioneel)",
      submit: "Account aanmaken",
      submitting: "Aanmaken…",
      success: "Uw account is klaar. U kunt nu inloggen met uw gebruikersnaam en wachtwoord.",
      errors: {
        code: "Die uitnodigingscode is ongeldig of verlopen. Controleer hem bij uw kliniekbeheerder.",
        username: "Die gebruikersnaam is al in gebruik. Kies een andere.",
        validation: "Controleer het formulier — een e-mail en een code zijn vereist.",
        server: "De registratie kon nu niet worden voltooid. Probeer het zo dadelijk opnieuw.",
      },
    },
    rejected: (n) => `${n} citaat${n === 1 ? "" : "en"} ${n === 1 ? "is" : "zijn"} als niet-verifieerbaar afgewezen en ${n === 1 ? "wordt" : "worden"} niet getoond.`,
    superseded: (label) => (label ? `Deze editie is vervangen door ${label}.` : "Deze editie is vervangen."),
    unreadable: (pages) => `De pagina's ${pages} van dit document konden niet worden gelezen en zijn hierboven niet weergegeven.`,
    damagedPageQuote: "Een deel van deze pagina kon niet worden gelezen, dus een voorbehoud dat naast deze passage is afgedrukt, kan erin ontbreken. Open de bronpagina voordat u erop vertrouwt.",
    combined: {
      heading: "Volledige tekst — de eigen woorden van de richtlijn, samengebracht",
      note: "Dezelfde letterlijke citaten hierboven, hier verzameld om op één plek te lezen. Geen samenvatting en niet samengevoegd tot één uitspraak — elk is een afzonderlijk fragment, bewaard bij zijn pagina.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
    pdf: {
      cited: "geciteerd", unreadablePage: "onleesbare pagina", prev: "‹ Vorige", next: "Volgende ›",
      close: "Sluiten", page: "Pagina", of: "van", loading: "Bron laden…",
      error: (m) => `Kon de bron-PDF niet laden: ${m}. Draait de backend?`,
    },
    translation: {
      label: "Vertalen:",
      banner: (l) => `Machinevertaling · ${l} · niet de woorden van de richtlijn, niet geverifieerd`,
      footnote: "Het letterlijke citaat hierboven is het document. Toets alles waarop u handelt eraan.",
      failed: "Vertaling mislukt.",
      rateLimited: "De vertaaldienst heeft zijn aanvraaglimiet bereikt. Probeer het later — het citaat hierboven blijft ongewijzigd.",
    },
  },
  ro: {
    legalTableNotice: "Tabelele de onorarii (Honorartafeln) nu sunt citate: anteturile coloanelor nu sunt preluate împreună cu rândurile, așa că o cifră ar putea apărea sub zona de onorariu greșită. Întrebați de regulă, nu de sumă.",
    noAnswer: {
      no_relevant_sources: "Niciun ghid din corpus nu acoperă această întrebare.",
      sources_do_not_answer: "Au fost găsite ghiduri, dar niciunul dintre pasajele lor nu răspunde la această întrebare.",
      verification_failed: "A fost produs un răspuns, dar nu a putut fi verificat față de sursa sa, așa că este reținut. Este o defecțiune a sistemului, nu absența unui ghid.",
      table_not_citable: "Răspunsul se află într-un tabel, iar acest sistem nu îl poate încă cita împreună cu anteturile coloanelor care dau sens numerelor de categorie. În loc să arate un număr fără anteturul său, este reținut — deschideți pagina sursă pentru a citi tabelul direct.",
      fallback: "Niciun răspuns.",
      hint: "Puneți o întrebare mai specifică, sau formulați-o așa cum sunt scrise aceste ghiduri — de exemplu, ce metodă contraceptivă se potrivește unei anumite afecțiuni, în loc de cum se tratează durerea în general.",
      incompleteSources: (s) => `Notă: o parte din ceea ce a fost căutat nu a putut fi citit. Nu au putut fi extrase pagini din ${s.join(", ")} — numărul dintre paranteze arată câte. Aceasta nu înseamnă că răspunsul se afla pe una dintre ele, dar sursa din spatele acestui răspuns este incompletă, așa că o tăcere aici este o dovadă mai slabă decât pare.`,
    },
    errors: {
      auth: "Sesiunea dvs. nu a putut fi autentificată. Autentificați-vă din nou, sau verificați dacă serviciul de autentificare funcționează.",
      server: "Serverul nu a răspuns. Poate porni sau este oprit — așteptați o clipă și încercați din nou.",
      request: (d) => `Cererea nu a putut fi procesată: ${d}`,
    },
    register: {
      heading: "Creați-vă contul",
      intro: "Înregistrarea se face pe bază de invitație. Introduceți codul primit de la administratorul clinicii dvs.; acesta vă va repartiza la clinica dvs.",
      code: "Cod de invitație",
      codeHint: "Obligatoriu. Decide cărei clinici îi aparține contul dvs.",
      email: "E-mail",
      password: "Parolă",
      name: "Nume (opțional)",
      submit: "Creați contul",
      submitting: "Se creează…",
      success: "Contul dvs. este gata. Vă puteți autentifica acum cu numele de utilizator și parola.",
      errors: {
        code: "Acest cod de invitație este invalid sau a expirat. Verificați-l cu administratorul clinicii dvs.",
        username: "Acest nume de utilizator este deja luat. Alegeți altul.",
        validation: "Verificați formularul — sunt necesare un e-mail și un cod.",
        server: "Înregistrarea nu a putut fi finalizată acum. Încercați din nou într-o clipă.",
      },
    },
    rejected: (n) => `${n} citat${n === 1 ? "" : "e"} ${n === 1 ? "a fost respins" : "au fost respinse"} ca neverificabil${n === 1 ? "" : "e"} și nu ${n === 1 ? "este afișat" : "sunt afișate"}.`,
    superseded: (label) => (label ? `Această ediție a fost înlocuită de ${label}.` : "Această ediție a fost înlocuită."),
    unreadable: (pages) => `Paginile ${pages} ale acestui document nu au putut fi citite și nu sunt reflectate mai sus.`,
    damagedPageQuote: "O parte din această pagină nu a putut fi citită, așa că o rezervă tipărită lângă acest pasaj ar putea lipsi din el. Deschideți pagina sursă înainte de a vă baza pe ea.",
    combined: {
      heading: "Text integral — cuvintele ghidului, adunate",
      note: "Aceleași citate textuale de mai sus, adunate aici pentru a fi citite într-un singur loc. Nu un rezumat și nu îmbinate într-o singură afirmație — fiecare este un fragment distinct, păstrat cu pagina sa.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
    pdf: {
      cited: "citat", unreadablePage: "pagină ilizibilă", prev: "‹ Înapoi", next: "Înainte ›",
      close: "Închide", page: "Pagina", of: "din", loading: "Se încarcă sursa…",
      error: (m) => `Nu s-a putut încărca PDF-ul sursă: ${m}. Backendul funcționează?`,
    },
    translation: {
      label: "Traduceți:",
      banner: (l) => `Traducere automată · ${l} · nu sunt cuvintele ghidului, neverificată`,
      footnote: "Citatul textual de mai sus este consemnarea. Verificați față de el tot ceea ce întreprindeți.",
      failed: "Traducerea a eșuat.",
      rateLimited: "Serviciul de traducere și-a atins limita de cereri. Încercați mai târziu — citatul de mai sus nu este afectat.",
    },
  },
  el: {
    legalTableNotice: "Οι πίνακες αμοιβών (Honorartafeln) δεν παρατίθενται: οι επικεφαλίδες των στηλών δεν μεταφέρονται μαζί με τις γραμμές, οπότε ένα ποσό θα μπορούσε να εμφανιστεί κάτω από λάθος ζώνη αμοιβής. Ρωτήστε για τον κανόνα, όχι για το ποσό.",
    noAnswer: {
      no_relevant_sources: "Καμία κατευθυντήρια οδηγία του σώματος κειμένων δεν καλύπτει αυτό το ερώτημα.",
      sources_do_not_answer: "Βρέθηκαν οδηγίες, αλλά κανένα από τα αποσπάσματά τους δεν απαντά σε αυτό το ερώτημα.",
      verification_failed: "Παρήχθη μια απάντηση αλλά δεν ήταν δυνατή η επαλήθευσή της έναντι της πηγής της, γι' αυτό αποκρύπτεται. Πρόκειται για σφάλμα του συστήματος, όχι για απουσία οδηγίας.",
      table_not_citable: "Η απάντηση βρίσκεται σε πίνακα, και το σύστημα δεν μπορεί ακόμη να την παραθέσει μαζί με τις επικεφαλίδες των στηλών που δίνουν νόημα στους αριθμούς κατηγορίας. Αντί να δείξει έναν αριθμό χωρίς την επικεφαλίδα του, αποκρύπτεται — ανοίξτε τη σελίδα της πηγής για να διαβάσετε τον πίνακα απευθείας.",
      fallback: "Καμία απάντηση.",
      hint: "Θέστε ένα πιο συγκεκριμένο ερώτημα, ή διατυπώστε το όπως είναι γραμμένες αυτές οι οδηγίες — για παράδειγμα, ποια αντισυλληπτική μέθοδος ταιριάζει σε μια συγκεκριμένη κατάσταση, αντί για το πώς αντιμετωπίζεται ο πόνος γενικά.",
      incompleteSources: (s) => `Σημείωση: μέρος αυτού που αναζητήθηκε δεν ήταν δυνατό να διαβαστεί. Δεν ήταν δυνατή η εξαγωγή σελίδων από ${s.join(", ")} — ο αριθμός στις παρενθέσεις δείχνει πόσες. Αυτό δεν σημαίνει ότι η απάντηση ήταν σε μία από αυτές, αλλά η πηγή πίσω από αυτή την απάντηση είναι ελλιπής, οπότε μια σιωπή εδώ είναι ασθενέστερη απόδειξη απ' ό,τι φαίνεται.`,
    },
    errors: {
      auth: "Δεν ήταν δυνατή η ταυτοποίηση της συνεδρίας σας. Συνδεθείτε ξανά, ή ελέγξτε ότι η υπηρεσία σύνδεσης λειτουργεί.",
      server: "Ο διακομιστής δεν απάντησε. Ίσως εκκινεί ή είναι εκτός λειτουργίας — περιμένετε μια στιγμή και δοκιμάστε ξανά.",
      request: (d) => `Δεν ήταν δυνατή η επεξεργασία του αιτήματος: ${d}`,
    },
    register: {
      heading: "Δημιουργήστε τον λογαριασμό σας",
      intro: "Η εγγραφή γίνεται κατόπιν πρόσκλησης. Εισαγάγετε τον κωδικό που σας έδωσε ο διαχειριστής της κλινικής σας· θα σας εντάξει στην κλινική σας.",
      code: "Κωδικός πρόσκλησης",
      codeHint: "Απαιτείται. Καθορίζει σε ποια κλινική ανήκει ο λογαριασμός σας.",
      email: "Email",
      password: "Κωδικός πρόσβασης",
      name: "Όνομα (προαιρετικό)",
      submit: "Δημιουργία λογαριασμού",
      submitting: "Δημιουργία…",
      success: "Ο λογαριασμός σας είναι έτοιμος. Μπορείτε τώρα να συνδεθείτε με το όνομα χρήστη και τον κωδικό σας.",
      errors: {
        code: "Αυτός ο κωδικός πρόσκλησης δεν είναι έγκυρος ή έχει λήξει. Ελέγξτε τον με τον διαχειριστή της κλινικής σας.",
        username: "Αυτό το όνομα χρήστη χρησιμοποιείται ήδη. Επιλέξτε άλλο.",
        validation: "Ελέγξτε τη φόρμα — απαιτούνται ένα email και ένας κωδικός.",
        server: "Η εγγραφή δεν ήταν δυνατό να ολοκληρωθεί αυτή τη στιγμή. Δοκιμάστε ξανά σε μια στιγμή.",
      },
    },
    rejected: (n) => `${n} παράθεση${n === 1 ? "" : "εις"} απορρίφθηκ${n === 1 ? "ε" : "αν"} ως μη επαληθεύσιμ${n === 1 ? "η" : "ες"} και δεν εμφανίζ${n === 1 ? "εται" : "ονται"}.`,
    superseded: (label) => (label ? `Αυτή η έκδοση αντικαταστάθηκε από ${label}.` : "Αυτή η έκδοση αντικαταστάθηκε."),
    unreadable: (pages) => `Οι σελίδες ${pages} αυτού του εγγράφου δεν ήταν δυνατό να διαβαστούν και δεν αποτυπώνονται παραπάνω.`,
    damagedPageQuote: "Μέρος αυτής της σελίδας δεν ήταν δυνατό να διαβαστεί, οπότε μια επιφύλαξη τυπωμένη δίπλα σε αυτό το απόσπασμα ίσως λείπει από αυτό. Ανοίξτε τη σελίδα της πηγής πριν βασιστείτε σε αυτό.",
    combined: {
      heading: "Πλήρες κείμενο — τα ίδια τα λόγια της οδηγίας, συγκεντρωμένα",
      note: "Οι ίδιες αυτούσιες παραθέσεις παραπάνω, συγκεντρωμένες εδώ για ανάγνωση σε ένα μέρος. Ούτε περίληψη ούτε ένωση σε μία δήλωση — καθεμία είναι ξεχωριστό απόσπασμα, φυλαγμένο με τη σελίδα του.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
    pdf: {
      cited: "παρατίθεται", unreadablePage: "μη αναγνώσιμη σελίδα", prev: "‹ Προηγ.", next: "Επόμ. ›",
      close: "Κλείσιμο", page: "Σελίδα", of: "από", loading: "Φόρτωση πηγής…",
      error: (m) => `Δεν ήταν δυνατή η φόρτωση του PDF της πηγής: ${m}. Εκτελείται το backend;`,
    },
    translation: {
      label: "Μετάφραση:",
      banner: (l) => `Αυτόματη μετάφραση · ${l} · όχι τα λόγια της οδηγίας, μη επαληθευμένη`,
      footnote: "Η αυτούσια παράθεση παραπάνω είναι το τεκμήριο. Ελέγξτε έναντί της οτιδήποτε πράττετε.",
      failed: "Η μετάφραση απέτυχε.",
      rateLimited: "Η υπηρεσία μετάφρασης έφτασε το όριο αιτημάτων της. Δοκιμάστε αργότερα — η παράθεση παραπάνω δεν επηρεάζεται.",
    },
  },
  cs: {
    legalTableNotice: "Tabulky honorářů (Honorartafeln) se necitují: záhlaví sloupců se s řádky nepřenášejí, takže by se částka mohla zobrazit pod nesprávnou honorářovou zónou. Ptejte se na pravidlo, nikoli na částku.",
    noAnswer: {
      no_relevant_sources: "Žádné doporučení v korpusu tuto otázku nepokrývá.",
      sources_do_not_answer: "Doporučení byla nalezena, ale žádná z jejich pasáží na tuto otázku neodpovídá.",
      verification_failed: "Odpověď byla vytvořena, ale nepodařilo se ji ověřit vůči jejímu zdroji, proto se zadržuje. Jde o selhání systému, nikoli o chybějící doporučení.",
      table_not_citable: "Odpověď je v tabulce a systém ji zatím neumí citovat spolu se záhlavími sloupců, která dávají číslům kategorií smysl. Místo zobrazení čísla bez záhlaví se zadržuje — otevřete zdrojovou stránku a přečtěte si tabulku přímo.",
      fallback: "Žádná odpověď.",
      hint: "Položte konkrétnější otázku, nebo ji formulujte tak, jak jsou tato doporučení napsána — například která antikoncepční metoda se hodí pro daný stav, místo jak léčit bolest obecně.",
      incompleteSources: (s) => `Poznámka: část prohledávaného se nepodařilo přečíst. Ze zdroje ${s.join(", ")} se nepodařilo extrahovat stránky — číslo v závorce udává kolik. Neznamená to, že odpověď byla na některé z nich, ale zdroj této odpovědi je neúplný, takže mlčení zde je slabší důkaz, než se zdá.`,
    },
    errors: {
      auth: "Vaši relaci se nepodařilo ověřit. Přihlaste se znovu, nebo zkontrolujte, zda běží přihlašovací služba.",
      server: "Server neodpověděl. Možná se spouští nebo je mimo provoz — chvíli počkejte a zkuste to znovu.",
      request: (d) => `Požadavek se nepodařilo zpracovat: ${d}`,
    },
    register: {
      heading: "Vytvořte si účet",
      intro: "Registrace je na pozvání. Zadejte kód, který vám dal správce vaší kliniky; zařadí vás do vaší kliniky.",
      code: "Kód pozvánky",
      codeHint: "Povinné. Určuje, do které kliniky váš účet patří.",
      email: "E-mail",
      password: "Heslo",
      name: "Jméno (nepovinné)",
      submit: "Vytvořit účet",
      submitting: "Vytváření…",
      success: "Váš účet je připraven. Nyní se můžete přihlásit svým uživatelským jménem a heslem.",
      errors: {
        code: "Tento kód pozvánky je neplatný nebo vypršel. Ověřte si jej u správce své kliniky.",
        username: "Toto uživatelské jméno je již obsazené. Zvolte jiné.",
        validation: "Zkontrolujte formulář — je vyžadován e-mail a kód.",
        server: "Registraci se teď nepodařilo dokončit. Zkuste to prosím za okamžik znovu.",
      },
    },
    rejected: (n) => `Jako neověřitelné bylo odmítnuto a nezobrazuje se citací: ${n}.`,
    superseded: (label) => (label ? `Toto vydání bylo nahrazeno vydáním ${label}.` : "Toto vydání bylo nahrazeno novějším."),
    unreadable: (pages) => `Stránky ${pages} tohoto dokumentu se nepodařilo přečíst a nejsou výše zohledněny.`,
    damagedPageQuote: "Část této stránky se nepodařilo přečíst, takže výhrada vytištěná vedle této pasáže v ní může chybět. Než se na ni spolehnete, otevřete zdrojovou stránku.",
    combined: {
      heading: "Úplné znění — vlastní slova doporučení, shromážděná",
      note: "Tytéž doslovné citace výše, shromážděné zde ke čtení na jednom místě. Nejde o shrnutí ani o spojení do jediného tvrzení — každá je samostatný úsek, uchovaný se svou stránkou.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
    pdf: {
      cited: "citováno", unreadablePage: "nečitelná stránka", prev: "‹ Předch.", next: "Další ›",
      close: "Zavřít", page: "Stránka", of: "z", loading: "Načítání zdroje…",
      error: (m) => `Zdrojové PDF se nepodařilo načíst: ${m}. Běží backend?`,
    },
    translation: {
      label: "Přeložit:",
      banner: (l) => `Strojový překlad · ${l} · nejsou to slova doporučení, neověřeno`,
      footnote: "Doslovná citace výše je záznam. Vše, podle čeho jednáte, si vůči ní ověřte.",
      failed: "Překlad se nezdařil.",
      rateLimited: "Překladová služba dosáhla svého limitu požadavků. Zkuste to později — citace výše zůstává beze změny.",
    },
  },
  hu: {
    legalTableNotice: "A díjtáblázatokat (Honorartafeln) nem idézzük: az oszlopfejlécek nem kerülnek át a sorokkal, így egy összeg a rossz díjzóna alatt jelenhetne meg. A szabályra kérdezzen rá, ne az összegre.",
    noAnswer: {
      no_relevant_sources: "A korpusz egyetlen irányelve sem fedi le ezt a kérdést.",
      sources_do_not_answer: "Találtunk irányelveket, de egyik szakaszuk sem válaszol erre a kérdésre.",
      verification_failed: "Válasz készült, de nem sikerült a forrásához mérten ellenőrizni, ezért visszatartjuk. Ez rendszerhiba, nem pedig egy irányelv hiánya.",
      table_not_citable: "A válasz egy táblázatban van, és a rendszer még nem tudja idézni azokkal az oszlopfejlécekkel együtt, amelyek a kategóriaszámoknak értelmet adnak. Ahelyett, hogy fejléc nélküli számot mutatna, visszatartja — nyissa meg a forrásoldalt, hogy közvetlenül olvassa a táblázatot.",
      fallback: "Nincs válasz.",
      hint: "Tegyen fel konkrétabb kérdést, vagy fogalmazza úgy, ahogy ezek az irányelvek íródtak — például melyik fogamzásgátló módszer való egy adott állapothoz, ahelyett hogy hogyan kezelendő a fájdalom általában.",
      incompleteSources: (s) => `Megjegyzés: a keresett anyag egy részét nem sikerült elolvasni. Nem sikerült oldalakat kinyerni innen: ${s.join(", ")} — a zárójelben lévő szám mutatja, hányat. Ez nem jelenti, hogy a válasz ezek egyikén volt, de a válasz mögötti forrás hiányos, így az itteni csend gyengébb bizonyíték, mint amilyennek látszik.`,
    },
    errors: {
      auth: "A munkamenetét nem sikerült hitelesíteni. Jelentkezzen be újra, vagy ellenőrizze, hogy fut-e a bejelentkezési szolgáltatás.",
      server: "A kiszolgáló nem válaszolt. Lehet, hogy éppen indul vagy leállt — várjon egy pillanatot, és próbálja újra.",
      request: (d) => `A kérést nem sikerült feldolgozni: ${d}`,
    },
    register: {
      heading: "Hozza létre a fiókját",
      intro: "A regisztráció meghívásos. Adja meg a klinikája adminisztrátorától kapott kódot; ez a klinikájához rendeli.",
      code: "Meghívókód",
      codeHint: "Kötelező. Ez dönti el, melyik klinikához tartozik a fiókja.",
      email: "E-mail",
      password: "Jelszó",
      name: "Név (nem kötelező)",
      submit: "Fiók létrehozása",
      submitting: "Létrehozás…",
      success: "A fiókja készen áll. Mostantól bejelentkezhet a felhasználónevével és jelszavával.",
      errors: {
        code: "Ez a meghívókód érvénytelen vagy lejárt. Ellenőrizze a klinikája adminisztrátorával.",
        username: "Ez a felhasználónév már foglalt. Válasszon másikat.",
        validation: "Ellenőrizze az űrlapot — egy e-mail és egy kód szükséges.",
        server: "A regisztrációt most nem sikerült befejezni. Kérjük, próbálja újra egy pillanat múlva.",
      },
    },
    rejected: (n) => `${n} idézetet nem ellenőrizhetőként elutasítottunk, és nem jelenik meg.`,
    superseded: (label) => (label ? `Ezt a kiadást felváltotta a következő: ${label}.` : "Ezt a kiadást újabb váltotta fel."),
    unreadable: (pages) => `E dokumentum ${pages} oldalait nem sikerült elolvasni, és a fentiben nem tükröződnek.`,
    damagedPageQuote: "Ennek az oldalnak egy részét nem sikerült elolvasni, így egy e szakasz mellé nyomtatott kikötés hiányozhat belőle. Mielőtt rá támaszkodna, nyissa meg a forrásoldalt.",
    combined: {
      heading: "Teljes szöveg — az irányelv saját szavai, összegyűjtve",
      note: "Ugyanazok a szó szerinti idézetek fentről, ide gyűjtve, hogy egy helyen olvashatók legyenek. Nem összefoglaló és nem egyetlen állítássá fűzve — mindegyik külön részlet, a saját oldalával megőrizve.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
    pdf: {
      cited: "idézve", unreadablePage: "olvashatatlan oldal", prev: "‹ Előző", next: "Következő ›",
      close: "Bezárás", page: "Oldal", of: "/", loading: "Forrás betöltése…",
      error: (m) => `A forrás PDF-et nem sikerült betölteni: ${m}. Fut a backend?`,
    },
    translation: {
      label: "Fordítás:",
      banner: (l) => `Gépi fordítás · ${l} · nem az irányelv szavai, nincs ellenőrizve`,
      footnote: "A fenti szó szerinti idézet a hiteles feljegyzés. Amire támaszkodik, azt vesse össze vele.",
      failed: "A fordítás nem sikerült.",
      rateLimited: "A fordítószolgáltatás elérte a kéréskorlátját. Próbálja később — a fenti idézetet ez nem érinti.",
    },
  },
};

// The 17-language table: reviewed languages as-is, each additional language inheriting English and
// overriding its chrome, the two warning banners, and (per the owner's decision) the full remaining
// strings. `page.tsx` reads STRINGS[lang] exactly as before.
export const STRINGS: Record<UiLang, Strings> = {
  ...REVIEWED,
  ...(Object.fromEntries(
    (Object.keys(CHROME) as (keyof typeof CHROME)[]).map((code) => [
      code,
      { ...REVIEWED.en, ...CHROME[code], ...NOTICES[code], ...DEEP[code] },
    ]),
  ) as Record<Exclude<UiLang, "en" | "de" | "ka">, Strings>),
};
