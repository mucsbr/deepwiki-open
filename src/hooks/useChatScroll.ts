'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { createScrollFollower } from '@/utils/chatScroll';

export function useChatScroll(resetKey: string) {
  const transcript = useRef<HTMLDivElement>(null);
  const transcriptContent = useRef<HTMLDivElement>(null);
  const follower = useRef<ReturnType<typeof createScrollFollower> | null>(null);
  const [following, setFollowing] = useState(true);

  useEffect(() => {
    if (!transcript.current || !transcriptContent.current) return;
    const current = createScrollFollower(transcript.current, transcriptContent.current, setFollowing);
    follower.current = current;
    return () => { current.dispose(); follower.current = null; };
  }, []);

  useEffect(() => {
    setFollowing(true);
    follower.current?.jump(false);
  }, [resetKey]);

  const jumpToLatest = useCallback(() => follower.current?.jump(), []);
  return { transcript, transcriptContent, following, jumpToLatest };
}
