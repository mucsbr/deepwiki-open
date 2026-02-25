'use client';

import React from 'react';
import ProcessedProjects from '@/components/ProcessedProjects';
import { useLanguage } from '@/contexts/LanguageContext';

export default function WikiProjectsPage() {
  const { messages } = useLanguage();

  return (
    <div className="min-h-screen bg-[var(--background)] container mx-auto p-4 md:p-8">
      <ProcessedProjects
        showHeader={true}
        messages={messages}
        className=""
      />
    </div>
  );
}