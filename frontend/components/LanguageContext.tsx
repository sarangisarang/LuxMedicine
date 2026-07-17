"use client";

import { createContext, useContext } from "react";

import { STRINGS, type UiLang } from "@/lib/i18n";

// One selector drives both the interface language and the `language` hint recorded on the
// query. Context rather than prop-drilling: the strings are needed six components deep (the
// staleness banner, the unreadable-pages note, the PDF viewer's controls) and threading a
// locale through every one of them is how a component quietly gets left in English.

const LanguageContext = createContext<UiLang>("en");

export function LanguageProvider({
  lang,
  children,
}: {
  lang: UiLang;
  children: React.ReactNode;
}) {
  return <LanguageContext.Provider value={lang}>{children}</LanguageContext.Provider>;
}

/** The chosen strings for the current language. See lib/i18n.ts — these are not generated. */
export function useT() {
  return STRINGS[useContext(LanguageContext)];
}

export function useLang(): UiLang {
  return useContext(LanguageContext);
}
