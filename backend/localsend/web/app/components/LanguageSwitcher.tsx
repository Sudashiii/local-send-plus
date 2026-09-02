"use client";

import { useLanguage } from "../context/LanguageContext";
export function LanguageSwitcher() {
  const { locale, setLocale, t } = useLanguage();

  return (
    <div className="flex items-center gap-1 rounded border border-zinc-200 bg-zinc-50 p-0.5 dark:border-zinc-700 dark:bg-zinc-800">
      <button
        type="button"
        onClick={() => setLocale("zh")}
        className={`rounded px-2 py-1 text-sm transition-colors ${
          locale === "zh"
            ? "bg-white font-medium text-zinc-900 shadow dark:bg-zinc-700 dark:text-white"
            : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-white"
        }`}
        aria-label="中文"
      >
        {t("lang.zh")}
      </button>
      <button
        type="button"
        onClick={() => setLocale("en")}
        className={`rounded px-2 py-1 text-sm transition-colors ${
          locale === "en"
            ? "bg-white font-medium text-zinc-900 shadow dark:bg-zinc-700 dark:text-white"
            : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-white"
        }`}
        aria-label="English"
      >
        {t("lang.en")}
      </button>
    </div>
  );
}
