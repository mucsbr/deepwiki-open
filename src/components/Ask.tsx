'use client';

import { useState } from 'react';
import { useLanguage } from '@/contexts/LanguageContext';
import LegacyAsk from './LegacyAsk';
import AgentChat from './agent/AgentChat';
import type { AskProps } from './agent/types';

export default function Ask(props: AskProps) {
  const [legacy, setLegacy] = useState(false);
  const { messages } = useLanguage();
  if (props.repoInfo.type !== 'gitlab') return <LegacyAsk {...props} />;
  return <div>
    <div className="flex justify-end px-3 py-2">
      <button className="text-xs text-[var(--muted)] hover:text-[var(--accent-primary)]" onClick={() => setLegacy(value => !value)}>
        {legacy ? (messages.agent?.title || 'Code analysis agent') : (messages.agent?.legacy || 'Classic Ask')}
      </button>
    </div>
    {legacy ? <LegacyAsk {...props} /> : <AgentChat {...props} />}
  </div>;
}
