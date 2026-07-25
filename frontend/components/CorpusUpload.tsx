"use client";

import { useRef, useState } from "react";

import type { BatchStatus } from "@/lib/api";
import type { UiLang } from "@/lib/i18n";

// The issuing organisations the backend accepts (app/core/vocabulary.py IssuingOrg). The sector is
// derived from this on the backend, never chosen here — the sector-drift discipline. Kept in sync by
// hand for now; a generated list is the follow-up, like the api types.
const ORGS = [
  "CDC", "NHLBI", "ESC", "AHA", "ACC", "WHO", "NICE", "ADA", "EASD", "ESMO", "ASCO", "IDSA",
  "KDIGO", "GINA", "Bundesrecht",
];

// Local strings, not the shared STRINGS: this is an admin-only tool and keeping its copy here avoids
// widening the typed Strings interface across three languages. Merge into lib/i18n.ts if it grows.
type UploadStrings = {
  section: string;
  hint: string;
  folder: string;
  chosen: (n: number) => string;
  org: string;
  versionLabel: string;
  license: string;
  licensePublicDomain: string;
  licenseLicensed: string;
  licenseUnknown: string;
  quarantineHint: string;
  source: string;
  sourcePlaceholder: string;
  submit: string;
  working: string;
  needFiles: string;
  needSource: string;
  serverError: string;
  done: string;
  failed: string;
};

// en/de/ka only; the 14 additional UI languages fall back to English here (admin chrome, low value
// to translate 17×). Fall back via `?? .en` at the call site.
const UPLOAD_STRINGS: Partial<Record<UiLang, UploadStrings>> & { en: UploadStrings } = {
  en: {
    section: "Add guidelines (admin)",
    hint: "Choose a folder of PDFs. Files over 120 pages are split into parts, then each is stored, extracted, and indexed.",
    folder: "Choose folder",
    chosen: (n) => `${n} PDF${n === 1 ? "" : "s"} selected`,
    org: "Issuing organisation",
    versionLabel: "Version label",
    license: "Licence",
    licensePublicDomain: "Public domain",
    licenseLicensed: "Licensed",
    licenseUnknown: "Unknown / unsure",
    quarantineHint: "Without an affirmed licence, uploads are quarantined — indexed but not searchable — until a licence is confirmed.",
    source: "Source",
    sourcePlaceholder: "Where these came from and why they may be indexed.",
    submit: "Upload & ingest",
    working: "Ingesting…",
    needFiles: "Choose a folder that contains at least one PDF.",
    needSource: "A source is required.",
    serverError: "The server could not be reached. Try again in a moment.",
    done: "Done",
    failed: "Failed",
  },
  de: {
    section: "Leitlinien hinzufügen (Admin)",
    hint: "Ordner mit PDFs wählen. Dateien über 120 Seiten werden in Teile geteilt, dann gespeichert, extrahiert und indiziert.",
    folder: "Ordner wählen",
    chosen: (n) => `${n} PDF${n === 1 ? "" : "s"} ausgewählt`,
    org: "Herausgebende Organisation",
    versionLabel: "Versionskennung",
    license: "Lizenz",
    licensePublicDomain: "Gemeinfrei",
    licenseLicensed: "Lizenziert",
    licenseUnknown: "Unbekannt / unsicher",
    quarantineHint: "Ohne bestätigte Lizenz werden Uploads in Quarantäne gestellt — indiziert, aber nicht durchsuchbar — bis eine Lizenz bestätigt ist.",
    source: "Quelle",
    sourcePlaceholder: "Woher diese stammen und warum sie indiziert werden dürfen.",
    submit: "Hochladen & indizieren",
    working: "Wird indiziert…",
    needFiles: "Ordner mit mindestens einer PDF wählen.",
    needSource: "Eine Quelle ist erforderlich.",
    serverError: "Server nicht erreichbar. Bitte gleich erneut versuchen.",
    done: "Fertig",
    failed: "Fehlgeschlagen",
  },
  ka: {
    section: "სახელმძღვანელოების დამატება (ადმინი)",
    hint: "აირჩიე PDF-ების ფოლდერი. 120 გვერდზე მეტი ფაილი იჭრება ნაწილებად, შემდეგ ინახება, ამოიღება და ინდექსდება.",
    folder: "ფოლდერის არჩევა",
    chosen: (n) => `არჩეულია ${n} PDF`,
    org: "გამომცემი ორგანიზაცია",
    versionLabel: "ვერსიის იარლიყი",
    license: "ლიცენზია",
    licensePublicDomain: "Public domain",
    licenseLicensed: "ლიცენზირებული",
    licenseUnknown: "უცნობი / დარწმუნებული არ ვარ",
    quarantineHint: "დადასტურებული ლიცენზიის გარეშე ატვირთვა კარანტინში ხვდება — ინდექსდება, მაგრამ ვერ იძებნება — სანამ ლიცენზია არ დადასტურდება.",
    source: "წყარო",
    sourcePlaceholder: "საიდან არის და რატომ შეიძლება ინდექსირება.",
    submit: "ატვირთვა და ინდექსირება",
    working: "მიმდინარეობს ინდექსირება…",
    needFiles: "აირჩიე ფოლდერი, სადაც მინიმუმ ერთი PDF-ია.",
    needSource: "წყაროს მითითება სავალდებულოა.",
    serverError: "სერვერი მიუწვდომელია. სცადე ცოტა ხანში.",
    done: "დასრულდა",
    failed: "ჩავარდა",
  },
  ru: { section: "Добавить руководства (админ)", hint: "Выберите папку с PDF. Файлы более 120 страниц делятся на части, затем сохраняются, извлекаются и индексируются.", folder: "Выбрать папку", chosen: (n) => `Выбрано PDF: ${n}`, org: "Издающая организация", versionLabel: "Метка версии", license: "Лицензия", licensePublicDomain: "Общественное достояние", licenseLicensed: "Лицензировано", licenseUnknown: "Неизвестно / не уверен", quarantineHint: "Без подтверждённой лицензии загрузки помещаются в карантин — индексируются, но не доступны для поиска — до подтверждения лицензии.", source: "Источник", sourcePlaceholder: "Откуда это и почему можно индексировать.", submit: "Загрузить и индексировать", working: "Индексация…", needFiles: "Выберите папку, содержащую хотя бы один PDF.", needSource: "Требуется источник.", serverError: "Сервер недоступен. Попробуйте позже.", done: "Готово", failed: "Ошибка" },
  uk: { section: "Додати настанови (адмін)", hint: "Виберіть теку з PDF. Файли понад 120 сторінок діляться на частини, потім зберігаються, витягуються та індексуються.", folder: "Вибрати теку", chosen: (n) => `Вибрано PDF: ${n}`, org: "Видавнича організація", versionLabel: "Мітка версії", license: "Ліцензія", licensePublicDomain: "Суспільне надбання", licenseLicensed: "Ліцензовано", licenseUnknown: "Невідомо / не впевнений", quarantineHint: "Без підтвердженої ліцензії завантаження потрапляють у карантин — індексуються, але недоступні для пошуку — до підтвердження ліцензії.", source: "Джерело", sourcePlaceholder: "Звідки це і чому можна індексувати.", submit: "Завантажити та індексувати", working: "Індексація…", needFiles: "Виберіть теку, що містить хоча б один PDF.", needSource: "Потрібне джерело.", serverError: "Сервер недоступний. Спробуйте пізніше.", done: "Готово", failed: "Помилка" },
  fr: { section: "Ajouter des recommandations (admin)", hint: "Choisissez un dossier de PDF. Les fichiers de plus de 120 pages sont divisés en parties, puis stockés, extraits et indexés.", folder: "Choisir un dossier", chosen: (n) => `${n} PDF sélectionné${n === 1 ? "" : "s"}`, org: "Organisation émettrice", versionLabel: "Étiquette de version", license: "Licence", licensePublicDomain: "Domaine public", licenseLicensed: "Sous licence", licenseUnknown: "Inconnu / incertain", quarantineHint: "Sans licence confirmée, les téléversements sont mis en quarantaine — indexés mais non consultables — jusqu'à confirmation d'une licence.", source: "Source", sourcePlaceholder: "D'où cela provient et pourquoi cela peut être indexé.", submit: "Téléverser et indexer", working: "Indexation…", needFiles: "Choisissez un dossier contenant au moins un PDF.", needSource: "Une source est requise.", serverError: "Serveur injoignable. Réessayez dans un instant.", done: "Terminé", failed: "Échec" },
  es: { section: "Añadir guías (admin)", hint: "Elija una carpeta de PDF. Los archivos de más de 120 páginas se dividen en partes, luego se almacenan, extraen e indexan.", folder: "Elegir carpeta", chosen: (n) => `${n} PDF seleccionado${n === 1 ? "" : "s"}`, org: "Organización emisora", versionLabel: "Etiqueta de versión", license: "Licencia", licensePublicDomain: "Dominio público", licenseLicensed: "Con licencia", licenseUnknown: "Desconocido / no seguro", quarantineHint: "Sin una licencia confirmada, las cargas se ponen en cuarentena — indexadas pero no consultables — hasta confirmar una licencia.", source: "Fuente", sourcePlaceholder: "De dónde proviene y por qué se puede indexar.", submit: "Subir e indexar", working: "Indexando…", needFiles: "Elija una carpeta que contenga al menos un PDF.", needSource: "Se requiere una fuente.", serverError: "No se pudo contactar con el servidor. Inténtelo de nuevo.", done: "Listo", failed: "Error" },
  it: { section: "Aggiungi linee guida (admin)", hint: "Scegli una cartella di PDF. I file con più di 120 pagine vengono divisi in parti, poi archiviati, estratti e indicizzati.", folder: "Scegli cartella", chosen: (n) => `${n} PDF selezionat${n === 1 ? "o" : "i"}`, org: "Organizzazione emittente", versionLabel: "Etichetta versione", license: "Licenza", licensePublicDomain: "Dominio pubblico", licenseLicensed: "Concesso in licenza", licenseUnknown: "Sconosciuto / incerto", quarantineHint: "Senza una licenza confermata, i caricamenti vengono messi in quarantena — indicizzati ma non ricercabili — fino alla conferma di una licenza.", source: "Fonte", sourcePlaceholder: "Da dove proviene e perché può essere indicizzato.", submit: "Carica e indicizza", working: "Indicizzazione…", needFiles: "Scegli una cartella che contenga almeno un PDF.", needSource: "È richiesta una fonte.", serverError: "Server irraggiungibile. Riprova tra poco.", done: "Fatto", failed: "Errore" },
  pl: { section: "Dodaj wytyczne (admin)", hint: "Wybierz folder z plikami PDF. Pliki powyżej 120 stron są dzielone na części, a następnie zapisywane, ekstrahowane i indeksowane.", folder: "Wybierz folder", chosen: (n) => `Wybrano PDF: ${n}`, org: "Organizacja wydająca", versionLabel: "Etykieta wersji", license: "Licencja", licensePublicDomain: "Domena publiczna", licenseLicensed: "Licencjonowane", licenseUnknown: "Nieznane / niepewne", quarantineHint: "Bez potwierdzonej licencji przesłane pliki trafiają do kwarantanny — indeksowane, ale niedostępne w wyszukiwaniu — do potwierdzenia licencji.", source: "Źródło", sourcePlaceholder: "Skąd pochodzą i dlaczego mogą być indeksowane.", submit: "Prześlij i indeksuj", working: "Indeksowanie…", needFiles: "Wybierz folder zawierający co najmniej jeden PDF.", needSource: "Źródło jest wymagane.", serverError: "Nie można połączyć się z serwerem. Spróbuj ponownie.", done: "Gotowe", failed: "Błąd" },
  tr: { section: "Kılavuz ekle (yönetici)", hint: "Bir PDF klasörü seçin. 120 sayfadan uzun dosyalar parçalara bölünür, ardından saklanır, çıkarılır ve dizinlenir.", folder: "Klasör seç", chosen: (n) => `${n} PDF seçildi`, org: "Yayınlayan kuruluş", versionLabel: "Sürüm etiketi", license: "Lisans", licensePublicDomain: "Kamu malı", licenseLicensed: "Lisanslı", licenseUnknown: "Bilinmiyor / emin değil", quarantineHint: "Onaylanmış lisans olmadan yüklemeler karantinaya alınır — dizinlenir ancak aranamaz — bir lisans onaylanana kadar.", source: "Kaynak", sourcePlaceholder: "Nereden geldiği ve neden dizinlenebileceği.", submit: "Yükle ve dizinle", working: "Dizinleniyor…", needFiles: "En az bir PDF içeren bir klasör seçin.", needSource: "Kaynak gereklidir.", serverError: "Sunucuya ulaşılamadı. Birazdan tekrar deneyin.", done: "Tamam", failed: "Başarısız" },
  ar: { section: "إضافة إرشادات (المسؤول)", hint: "اختر مجلدًا من ملفات PDF. الملفات التي تزيد عن 120 صفحة تُقسَّم إلى أجزاء، ثم تُخزَّن وتُستخرَج وتُفهرَس.", folder: "اختر مجلدًا", chosen: (n) => `تم اختيار ${n} ملف PDF`, org: "الجهة المُصدِرة", versionLabel: "وسم الإصدار", license: "الترخيص", licensePublicDomain: "ملكية عامة", licenseLicensed: "مُرخَّص", licenseUnknown: "غير معروف / غير متأكد", quarantineHint: "بدون ترخيص مؤكَّد، تُوضع الملفات المرفوعة في الحجر — تُفهرَس لكن لا يمكن البحث فيها — حتى تأكيد الترخيص.", source: "المصدر", sourcePlaceholder: "من أين أتت ولماذا يمكن فهرستها.", submit: "رفع وفهرسة", working: "جارٍ الفهرسة…", needFiles: "اختر مجلدًا يحتوي على ملف PDF واحد على الأقل.", needSource: "المصدر مطلوب.", serverError: "تعذّر الوصول إلى الخادم. حاول مرة أخرى بعد قليل.", done: "تم", failed: "فشل" },
  pt: { section: "Adicionar diretrizes (admin)", hint: "Escolha uma pasta de PDF. Ficheiros com mais de 120 páginas são divididos em partes, depois armazenados, extraídos e indexados.", folder: "Escolher pasta", chosen: (n) => `${n} PDF selecionado${n === 1 ? "" : "s"}`, org: "Organização emissora", versionLabel: "Etiqueta de versão", license: "Licença", licensePublicDomain: "Domínio público", licenseLicensed: "Licenciado", licenseUnknown: "Desconhecido / incerto", quarantineHint: "Sem uma licença confirmada, os carregamentos ficam em quarentena — indexados mas não pesquisáveis — até confirmação de uma licença.", source: "Fonte", sourcePlaceholder: "De onde vieram e porque podem ser indexados.", submit: "Carregar e indexar", working: "A indexar…", needFiles: "Escolha uma pasta que contenha pelo menos um PDF.", needSource: "É necessária uma fonte.", serverError: "Não foi possível contactar o servidor. Tente novamente.", done: "Concluído", failed: "Falha" },
  nl: { section: "Richtlijnen toevoegen (beheerder)", hint: "Kies een map met pdf's. Bestanden van meer dan 120 pagina's worden in delen gesplitst, dan opgeslagen, geëxtraheerd en geïndexeerd.", folder: "Map kiezen", chosen: (n) => `${n} pdf geselecteerd`, org: "Uitgevende organisatie", versionLabel: "Versielabel", license: "Licentie", licensePublicDomain: "Publiek domein", licenseLicensed: "Gelicentieerd", licenseUnknown: "Onbekend / onzeker", quarantineHint: "Zonder bevestigde licentie worden uploads in quarantaine geplaatst — geïndexeerd maar niet doorzoekbaar — totdat een licentie is bevestigd.", source: "Bron", sourcePlaceholder: "Waar deze vandaan komen en waarom ze geïndexeerd mogen worden.", submit: "Uploaden en indexeren", working: "Indexeren…", needFiles: "Kies een map met ten minste één pdf.", needSource: "Een bron is vereist.", serverError: "Server niet bereikbaar. Probeer het zo opnieuw.", done: "Klaar", failed: "Mislukt" },
  ro: { section: "Adaugă ghiduri (admin)", hint: "Alege un folder cu PDF-uri. Fișierele cu peste 120 de pagini sunt împărțite în părți, apoi stocate, extrase și indexate.", folder: "Alege folder", chosen: (n) => `${n} PDF selectat${n === 1 ? "" : "e"}`, org: "Organizația emitentă", versionLabel: "Etichetă versiune", license: "Licență", licensePublicDomain: "Domeniu public", licenseLicensed: "Licențiat", licenseUnknown: "Necunoscut / nesigur", quarantineHint: "Fără o licență confirmată, încărcările sunt puse în carantină — indexate, dar nu pot fi căutate — până la confirmarea unei licențe.", source: "Sursă", sourcePlaceholder: "De unde provin și de ce pot fi indexate.", submit: "Încarcă și indexează", working: "Se indexează…", needFiles: "Alege un folder care conține cel puțin un PDF.", needSource: "Este necesară o sursă.", serverError: "Serverul nu a putut fi contactat. Încearcă din nou.", done: "Gata", failed: "Eșuat" },
  el: { section: "Προσθήκη οδηγιών (διαχειριστής)", hint: "Επιλέξτε έναν φάκελο με PDF. Τα αρχεία άνω των 120 σελίδων χωρίζονται σε μέρη, μετά αποθηκεύονται, εξάγονται και ευρετηριάζονται.", folder: "Επιλογή φακέλου", chosen: (n) => `Επιλέχθηκαν ${n} PDF`, org: "Οργανισμός έκδοσης", versionLabel: "Ετικέτα έκδοσης", license: "Άδεια", licensePublicDomain: "Δημόσιος τομέας", licenseLicensed: "Αδειοδοτημένο", licenseUnknown: "Άγνωστο / αβέβαιο", quarantineHint: "Χωρίς επιβεβαιωμένη άδεια, οι αναρτήσεις τίθενται σε καραντίνα — ευρετηριάζονται αλλά δεν αναζητούνται — μέχρι να επιβεβαιωθεί άδεια.", source: "Πηγή", sourcePlaceholder: "Από πού προέρχονται και γιατί μπορούν να ευρετηριαστούν.", submit: "Μεταφόρτωση και ευρετηρίαση", working: "Ευρετηρίαση…", needFiles: "Επιλέξτε φάκελο που περιέχει τουλάχιστον ένα PDF.", needSource: "Απαιτείται πηγή.", serverError: "Ο διακομιστής δεν ήταν προσβάσιμος. Δοκιμάστε ξανά.", done: "Έτοιμο", failed: "Απέτυχε" },
  cs: { section: "Přidat doporučení (admin)", hint: "Vyberte složku s PDF. Soubory nad 120 stran se rozdělí na části, poté se uloží, extrahují a indexují.", folder: "Vybrat složku", chosen: (n) => `Vybráno PDF: ${n}`, org: "Vydávající organizace", versionLabel: "Označení verze", license: "Licence", licensePublicDomain: "Volné dílo", licenseLicensed: "Licencováno", licenseUnknown: "Neznámé / nejisté", quarantineHint: "Bez potvrzené licence se nahrané soubory umístí do karantény — indexují se, ale nelze v nich vyhledávat — dokud není licence potvrzena.", source: "Zdroj", sourcePlaceholder: "Odkud pocházejí a proč mohou být indexovány.", submit: "Nahrát a indexovat", working: "Indexování…", needFiles: "Vyberte složku obsahující alespoň jeden PDF.", needSource: "Zdroj je povinný.", serverError: "Server není dostupný. Zkuste to za chvíli.", done: "Hotovo", failed: "Chyba" },
  hu: { section: "Irányelvek hozzáadása (admin)", hint: "Válasszon egy PDF-mappát. A 120 oldalnál hosszabb fájlok részekre bomlanak, majd tárolódnak, kinyerődnek és indexelődnek.", folder: "Mappa választása", chosen: (n) => `${n} PDF kiválasztva`, org: "Kibocsátó szervezet", versionLabel: "Verziócímke", license: "Licenc", licensePublicDomain: "Közkincs", licenseLicensed: "Licencelt", licenseUnknown: "Ismeretlen / bizonytalan", quarantineHint: "Megerősített licenc nélkül a feltöltések karanténba kerülnek — indexelve, de nem kereshetők — a licenc megerősítéséig.", source: "Forrás", sourcePlaceholder: "Honnan származnak és miért indexelhetők.", submit: "Feltöltés és indexelés", working: "Indexelés…", needFiles: "Válasszon legalább egy PDF-et tartalmazó mappát.", needSource: "Forrás megadása kötelező.", serverError: "A szerver nem érhető el. Próbálja újra.", done: "Kész", failed: "Sikertelen" },
};

// The native <input type="file"> button ("Choose files"/"No file chosen") is drawn by the browser in
// the OS/browser language — it cannot be styled or translated. So the input is hidden and a real
// button drives it; this is the "nothing chosen yet" text beside that button, in the UI language.
const NONE_SELECTED: Record<UiLang, string> = {
  en: "No folder chosen", de: "Kein Ordner gewählt", ka: "ფოლდერი არ არის არჩეული",
  ru: "Папка не выбрана", uk: "Теку не вибрано", fr: "Aucun dossier choisi",
  es: "Ninguna carpeta elegida", it: "Nessuna cartella scelta", pl: "Nie wybrano folderu",
  tr: "Klasör seçilmedi", ar: "لم يتم اختيار مجلد", pt: "Nenhuma pasta escolhida",
  nl: "Geen map gekozen", ro: "Niciun folder ales", el: "Δεν επιλέχθηκε φάκελος",
  cs: "Není vybrána žádná složka", hu: "Nincs mappa kiválasztva",
};

export function CorpusUpload({ lang }: { lang: UiLang }) {
  const t = UPLOAD_STRINGS[lang] ?? UPLOAD_STRINGS.en;
  const noneSelected = NONE_SELECTED[lang] ?? NONE_SELECTED.en;
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [chosen, setChosen] = useState(0);
  const [org, setOrg] = useState("CDC");
  const [versionLabel, setVersionLabel] = useState("2026");
  const [licenseStatus, setLicenseStatus] = useState("public_domain");
  const [source, setSource] = useState("");
  const [job, setJob] = useState<BatchStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // webkitdirectory/directory are non-standard attributes. A ref callback sets them the instant the
  // node mounts — more reliable than a useEffect keyed on `open`, which could miss the mount. Falls
  // back gracefully: if a browser ignores webkitdirectory, the input still picks (multiple) files.
  const attachFolderInput = (el: HTMLInputElement | null) => {
    inputRef.current = el;
    if (el) {
      el.setAttribute("webkitdirectory", "");
      el.setAttribute("directory", "");
    }
  };

  function pdfsFrom(list: FileList | null): File[] {
    return list ? Array.from(list).filter((f) => f.name.toLowerCase().endsWith(".pdf")) : [];
  }

  async function poll(jobId: string) {
    try {
      const res = await fetch(`/api/ingest/${jobId}`, { cache: "no-store" });
      if (res.ok) {
        const status = (await res.json()) as BatchStatus;
        setJob(status);
        if (status.status === "done" || status.status === "failed") {
          setBusy(false);
          return;
        }
      }
    } catch {
      // A dropped poll is not fatal — keep polling; the job runs on the server regardless.
    }
    setTimeout(() => poll(jobId), 1500);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setJob(null);

    const pdfs = pdfsFrom(inputRef.current?.files ?? null);
    if (pdfs.length === 0) {
      setError(t.needFiles);
      return;
    }
    if (!source.trim()) {
      setError(t.needSource);
      return;
    }

    const form = new FormData();
    for (const file of pdfs) form.append("files", file, file.name);
    form.append("issuing_org", org);
    form.append("version_label", versionLabel);
    form.append("license_status", licenseStatus);
    form.append("provenance_source", source.trim());

    setBusy(true);
    try {
      const res = await fetch("/api/ingest", { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? `HTTP ${res.status}`);
        setBusy(false);
        return;
      }
      void poll(data.job_id as string);
    } catch {
      setError(t.serverError);
      setBusy(false);
    }
  }

  const barColour =
    job?.status === "failed"
      ? "bg-red-500"
      : job?.status === "done"
        ? "bg-emerald-500"
        : "bg-sky-500";

  return (
    <section className="mt-6 rounded-md border border-neutral-200 dark:border-neutral-800">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium"
      >
        <span>{t.section}</span>
        <span className="text-neutral-400">{open ? "–" : "+"}</span>
      </button>

      {open && (
        <div className="border-t border-neutral-200 px-4 py-4 dark:border-neutral-800">
          <p className="text-xs text-neutral-500">{t.hint}</p>

          <form onSubmit={submit} className="mt-4 space-y-3">
            <div className="flex items-center gap-3">
              {/* A <label> wrapping the input opens the picker natively on click — no JS `.click()`,
                  which is the reliable way to drive a visually-hidden file input across browsers. The
                  input still holds the files and stays keyboard-reachable; the label text is ours, so
                  it translates (unlike the browser's native file-button, which does not). */}
              <label className="cursor-pointer rounded-md border border-neutral-300 bg-neutral-100 px-3 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-800">
                {t.folder}
                <input
                  ref={attachFolderInput}
                  type="file"
                  multiple
                  onChange={(e) => setChosen(pdfsFrom(e.target.files).length)}
                  className="sr-only"
                />
              </label>
              <span className="text-xs text-neutral-500">
                {chosen > 0 ? t.chosen(chosen) : noneSelected}
              </span>
            </div>

            <div className="flex flex-wrap gap-3">
              <label className="text-xs text-neutral-500">
                {t.org}
                <select value={org} onChange={(e) => setOrg(e.target.value)} className="mt-1 block rounded-md border border-neutral-300 bg-transparent px-2 py-1 text-sm dark:border-neutral-700">
                  {ORGS.map((o) => (
                    <option key={o} value={o}>
                      {o}
                    </option>
                  ))}
                </select>
              </label>

              <label className="text-xs text-neutral-500">
                {t.versionLabel}
                <input value={versionLabel} onChange={(e) => setVersionLabel(e.target.value)} className="mt-1 block w-28 rounded-md border border-neutral-300 bg-transparent px-2 py-1 text-sm dark:border-neutral-700" />
              </label>
            </div>

            <label className="block text-xs text-neutral-500">
              {t.license}
              <select value={licenseStatus} onChange={(e) => setLicenseStatus(e.target.value)} className="mt-1 block rounded-md border border-neutral-300 bg-transparent px-2 py-1 text-sm dark:border-neutral-700">
                <option value="public_domain">{t.licensePublicDomain}</option>
                <option value="licensed">{t.licenseLicensed}</option>
                <option value="unknown">{t.licenseUnknown}</option>
              </select>
            </label>

            {licenseStatus === "unknown" && (
              <p className="text-xs text-amber-700 dark:text-amber-400">{t.quarantineHint}</p>
            )}

            <label className="block text-xs text-neutral-500">
              {t.source}
              <textarea value={source} onChange={(e) => setSource(e.target.value)} placeholder={t.sourcePlaceholder} rows={2} className="mt-1 block w-full rounded-md border border-neutral-300 bg-transparent px-2 py-1 text-sm dark:border-neutral-700" />
            </label>

            <button type="submit" disabled={busy} className="rounded-md bg-neutral-800 px-4 py-2 text-sm text-white disabled:opacity-50 dark:bg-neutral-200 dark:text-neutral-900">
              {busy ? t.working : t.submit}
            </button>
          </form>

          {error && <p className="mt-3 text-xs text-red-600 dark:text-red-400">{error}</p>}

          {job && (
            <div className="mt-4">
              <div className="h-2 w-full overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
                <div className={`h-full ${barColour} transition-all`} style={{ width: `${job.percent}%` }} />
              </div>
              <p className="mt-1 text-xs text-neutral-500">
                {job.percent}% — {job.status === "done" ? t.done : job.status === "failed" ? t.failed : job.phase}
              </p>
              <ul className="mt-2 space-y-1 text-xs">
                {job.files.map((f, i) => (
                  <li key={i} className={f.error ? "text-red-600 dark:text-red-400" : "text-neutral-500"}>
                    {f.filename}
                    {f.parts > 0 && ` — ${f.done_parts}/${f.parts}`}
                    {f.error && ` — ${f.error}`}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
