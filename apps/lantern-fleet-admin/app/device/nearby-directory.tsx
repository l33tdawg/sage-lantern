'use client';

import { useEffect, useState } from 'react';
import { communityRequest, parseRadioContacts, type RadioContact } from '../../lib/community';
import { requestRadioStatus } from '../../lib/radio-request';
import type { RadioStatus } from '../../lib/radio-status';
import { heardContactAge } from '../../lib/radio-directory';
import { getDeviceToken, subscribeDeviceSession } from '../../lib/device-session';
import QuestionKeyboard from './question-keyboard';
import { BackIcon, NetworkIcon } from './icons';

export default function NearbyDirectory({ close }: { close: () => void }) {
  const [contacts, setContacts] = useState<RadioContact[] | null>(null);
  const [observation, setObservation] = useState<{ value: RadioStatus; startedAt: number } | null>(null);
  const [now, setNow] = useState(0);
  const [more, setMore] = useState(false);
  const [error, setError] = useState(false);
  const [revision, setRevision] = useState(0);
  const [filter, setFilter] = useState('');
  const [typing, setTyping] = useState(false);
  useEffect(() => {
    const token = getDeviceToken();
    const controller = new AbortController();
    const clear = () => { controller.abort(); setContacts(null); setObservation(null); setFilter(''); setTyping(false); };
    const unsubscribe = subscribeDeviceSession(clear);
    const visibility = () => { if (document.hidden) clear(); };
    document.addEventListener('visibilitychange', visibility);
    const timeout = setTimeout(() => { controller.abort(); setError(true); }, 15000);
    setError(false); setContacts(null); setObservation(null);
    const clock = setInterval(() => setNow(performance.now()), 1000);
    void requestRadioStatus(controller.signal).then((value) => {
      if (!controller.signal.aborted && token === getDeviceToken() && !document.hidden) { setObservation(value); setNow(performance.now()); }
    }).catch(() => {});
    void communityRequest('contacts/radio', {}, controller.signal).then((value) => {
      if (controller.signal.aborted || !token || token !== getDeviceToken() || document.hidden) return;
      const result = parseRadioContacts(value);
      setContacts(result.items); setMore(result.more);
    }).catch(() => { if (!controller.signal.aborted) setError(true); }).finally(() => clearTimeout(timeout));
    return () => { clearInterval(clock); clearTimeout(timeout); controller.abort(); unsubscribe(); document.removeEventListener('visibilitychange', visibility); };
  }, [revision]);
  const matches = contacts?.filter((contact) => contact.expires_at > Date.now() / 1000 && [contact.name, contact.chain_id, contact.agent_id].some((value) => value.toLocaleLowerCase().includes(filter.toLocaleLowerCase())));
  return <section className="device-subscreen" aria-label="People directory">
    <header className="device-subheader"><button onClick={close}><BackIcon />Back</button><div className="device-page-title"><NetworkIcon /><h1>People</h1></div></header>
    <div className="device-subcontent">
      {typing ? <QuestionKeyboard initialText={filter} label="Find a name, SAGE key or network" submitLabel="Find" suggestions={[]} onChange={() => {}} onCancel={() => setTyping(false)} onSubmit={(value) => { setFilter(value); setTyping(false); }} canSubmit /> : <>
        <div className="device-panel"><button onClick={() => setTyping(true)}>Find people or SAGE key</button><button onClick={() => setRevision((value) => value + 1)}>Refresh</button>{filter && <button onClick={() => setFilter('')}>Show all</button>}</div>
        <p>Current shared contacts. This list does not show who is online or within radio range.</p>
        {error ? <p role="status">Directory unavailable. Try Refresh.</p> : contacts === null ? <p role="status">Loading people…</p> : matches?.length === 0 ? <p>{filter ? 'No match in loaded contacts.' : 'No shared contacts available yet.'}</p> : matches?.map((contact) => {
          const age = observation?.value.health ? heardContactAge(contact, contacts, observation.value.health.discovered_nodes, (now - observation.startedAt) / 1000, Date.now() / 1000) : null;
          return <details className="device-panel" key={contact.id}><summary>{contact.name}</summary><p>{age === null ? 'No recent matching radio sighting' : `Matching radio heard ${Math.floor(age)}s ago · may be relayed`}</p><p className="radio-node-key">{contact.chain_id} · {contact.agent_id}</p><p className="radio-node-key">Radio: {contact.radio_public_key}</p><p>Shared identity, not proof of a person’s real-world identity or direct radio range.</p></details>;
        })}
        {contacts && more && <p>Only the first {contacts.length} contacts are loaded. Search covers these shared contacts and networks only.</p>}
      </>}
    </div>
  </section>;
}
