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

// Which corpus a question is asked of. Mirrors the backend's Sector enum — the values are sent
// as-is and the backend rejects anything else, so this list cannot widen the search.
export type Sector = "medical" | "legal";

export const SECTORS: { code: Sector; label: Record<UiLang, string> }[] = [
  { code: "medical", label: { en: "Medicine", de: "Medizin", ka: "მედიცინა" } },
  { code: "legal", label: { en: "Law (Baurecht)", de: "Recht (Baurecht)", ka: "სამართალი" } },
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
  unreadable: (pages: string) => string;
  combined: {
    heading: string;
    // The load-bearing label: this is NOT a summary. It is the same verbatim quotes gathered,
    // each still its own span with its own page — never joined into one statement.
    note: string;
    pageRef: (org: string, pages: string) => string;
  };
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
    signOut: "Sign out",
    demoNotice:
      "Demo — do not enter real patient data. Answers are generated by a model whose inference is not EU-hosted in this preview.",
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
    combined: {
      heading: "Full text — the guideline's own words, gathered",
      note: "The same verbatim quotes above, collected here to read in one place. Not a summary and not joined into a single statement — each is a separate span, kept with its page.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
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
    signOut: "Abmelden",
    demoNotice:
      "Demo — keine echten Patientendaten eingeben. Antworten stammen von einem Modell, dessen Verarbeitung in dieser Vorschau nicht in der EU erfolgt.",
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
    // TODO(review): clinician-facing medical German — have a native speaker check before deploy.
    combined: {
      heading: "Gesamter Text — die Worte der Leitlinie, gesammelt",
      note: "Dieselben wörtlichen Zitate von oben, hier an einer Stelle gesammelt. Keine Zusammenfassung und nicht zu einer einzigen Aussage verbunden — jedes ist ein eigener Abschnitt, mit seiner Seite.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
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
    signOut: "გასვლა",
    demoNotice:
      "დემო — ნუ შეიყვანთ რეალურ პაციენტის მონაცემებს. პასუხებს გენერირებს მოდელი, რომლის დამუშავება ამ ვერსიაში EU-ში არ ხდება.",
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
    combined: {
      heading: "სრული ტექსტი — გაიდლაინის სიტყვები, თავმოყრილი",
      note: "ზემოთ მოცემული იგივე ვერბატიმ ციტატები, ერთ ადგილას შეკრებილი წასაკითხად. არა შეჯამება და არა ერთ დებულებად გაერთიანებული — თითოეული ცალკე ნაწყვეტია, თავის გვერდთან ერთად.",
      pageRef: (org, pages) => `${org} · ${pages}`,
    },
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
