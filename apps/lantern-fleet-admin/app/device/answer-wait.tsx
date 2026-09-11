'use client';

import { useEffect, useState, type CSSProperties } from 'react';
import { WAIT_PHRASE_INTERVAL_MS, workingMessages } from '../../lib/answer-wait';
import { LanternIcon } from './icons';

export default function AnswerWait({ stage, expanded = false }: { stage?: string | null; expanded?: boolean }) {
  const messages = workingMessages(stage);
  const count = messages.length;
  const [activeStage, setActiveStage] = useState(stage);
  const [index, setIndex] = useState(0);
  // Reset wording immediately without remounting the panel: its transition must
  // survive the real stage change when the first answer text arrives.
  if (activeStage !== stage) {
    setActiveStage(stage);
    setIndex(0);
  }
  useEffect(() => {
    const timer = setInterval(() => setIndex((current) => (current + 1) % count), WAIT_PHRASE_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [stage, count]);

  const phrase = <span className="device-wait-copy" aria-hidden="true">
      {/* Reserve the longest phrase's space so rotation cannot move the chat. */}
      {messages.map((message) => <span className="device-wait-reserve" key={message}>{message}</span>)}
      <span className="device-wait-phrase" key={`${stage || 'unknown'}-${index}`}>{messages[index]}</span>
    </span>;
  return <div className={`device-answer-wait${expanded ? ' device-answer-wait-expanded' : ''}`} style={{ '--wait-cycle': `${WAIT_PHRASE_INTERVAL_MS}ms` } as CSSProperties}>
    <div className="device-wait-inline" aria-hidden="true"><span className="device-wait-breath" />{phrase}</div>
    <div className="device-wait-panel" aria-hidden="true">
      <span className="device-wait-lantern"><LanternIcon /></span>
      <div className="device-wait-panel-copy"><span className="device-wait-title">Working on your question</span>{phrase}</div>
    </div>
    {/* Announce actual stage changes, not decorative wording every three seconds. */}
    <span className="device-wait-accessible" role="status" aria-live="polite" aria-atomic="true">{messages[0]}</span>
  </div>;
}
