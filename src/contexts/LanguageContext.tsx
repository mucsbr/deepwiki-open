/* eslint-disable @typescript-eslint/no-explicit-any */
'use client';

import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { locales } from '@/i18n';

type Messages = Record<string, any>;
type LanguageContextType = {
  language: string;
  setLanguage: (lang: string) => void;
  messages: Messages;
  supportedLanguages: Record<string, string>;
};

const LanguageContext = createContext<LanguageContextType | undefined>(undefined);

export function LanguageProvider({ children }: { children: ReactNode }) {
  // Initialize with 'zh'
  const [language, setLanguageState] = useState<string>('zh');
  const [messages, setMessages] = useState<Messages>({});
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [supportedLanguages, setSupportedLanguages] = useState({})
  const [defaultLanguage, setDefaultLanguage] = useState('zh')

  // Always use zh as the default language
  const detectBrowserLanguage = (): string => {
    return 'zh';
  };

  useEffect(() => {
    const getSupportedLanguages = async () => {
      try {
        const response = await fetch('/api/lang/config');
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        setSupportedLanguages(data.supported_languages);
        setDefaultLanguage(data.default);
      } catch (err) {
        console.error("Failed to fetch auth status:", err);
        // Assuming auth is required if fetch fails to avoid blocking UI for safety
        const defaultSupportedLanguages = {
          "en": "English",
          "ja": "Japanese (日本語)",
          "zh": "Mandarin Chinese (中文)",
          "zh-tw": "Traditional Chinese (繁體中文)",
          "es": "Spanish (Español)",
          "kr": "Korean (한국어)",
          "vi": "Vietnamese (Tiếng Việt)",
          "pt-br": "Brazilian Portuguese (Português Brasileiro)",
          "fr": "Français (French)",
          "ru": "Русский (Russian)"
        };
        setSupportedLanguages(defaultSupportedLanguages);
        setDefaultLanguage("zh");
      }
    }
    getSupportedLanguages();
  }, []);

  useEffect(() => {
    if (Object.keys(supportedLanguages).length > 0) {
      const loadLanguage = async () => {
        try {
          // Only access localStorage in the browser
          let storedLanguage;
          if (typeof window !== 'undefined') {
            storedLanguage = localStorage.getItem('language');
    
            // If no language is stored, detect browser language
            if (!storedLanguage) {
              console.log('No language in localStorage, detecting browser language');
              storedLanguage = detectBrowserLanguage();
    
              // Store the detected language
              localStorage.setItem('language', storedLanguage);
            }
          } else {
            console.log('Running on server-side, using default language');
            storedLanguage = 'zh';
          }
    
          console.log('Supported languages loaded, validating language:', storedLanguage);
          const validLanguage = Object.keys(supportedLanguages).includes(storedLanguage as any) ? storedLanguage : defaultLanguage;
          console.log('Valid language determined:', validLanguage);
    
          // Load messages for the language
          const langMessages = (await import(`../messages/${validLanguage}.json`)).default;
    
          setLanguageState(validLanguage);
          setMessages(langMessages);
    
          // Update HTML lang attribute (only in browser)
          if (typeof document !== 'undefined') {
            document.documentElement.lang = validLanguage;
          }
        } catch (error) {
          console.error('Failed to load language:', error);
          // Fallback to Chinese
          console.log('Falling back to Chinese due to error');
          const zhMessages = (await import('../messages/zh.json')).default;
          setMessages(zhMessages);
        } finally {
          setIsLoading(false);
        }
      };
      
      loadLanguage();
    }
  }, [supportedLanguages, defaultLanguage]);

  // Update language and load new messages
  const setLanguage = async (lang: string) => {
    try {
      console.log('Setting language to:', lang);
      const validLanguage = Object.keys(supportedLanguages).includes(lang as any) ? lang : defaultLanguage;

      // Load messages for the new language
      const langMessages = (await import(`../messages/${validLanguage}.json`)).default;

      setLanguageState(validLanguage);
      setMessages(langMessages);

      // Store in localStorage (only in browser)
      if (typeof window !== 'undefined') {
        localStorage.setItem('language', validLanguage);
      }

      // Update HTML lang attribute (only in browser)
      if (typeof document !== 'undefined') {
        document.documentElement.lang = validLanguage;
      }
    } catch (error) {
      console.error('Failed to set language:', error);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-gray-100 dark:bg-gray-900">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-purple-500 mx-auto mb-4"></div>
          <p className="text-gray-600 dark:text-gray-400">Loading...</p>
        </div>
      </div>
    );
  }

  return (
    <LanguageContext.Provider value={{ language, setLanguage, messages, supportedLanguages }}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useLanguage() {
  const context = useContext(LanguageContext);
  if (context === undefined) {
    throw new Error('useLanguage must be used within a LanguageProvider');
  }
  return context;
}
