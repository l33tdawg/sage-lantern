import type { CommunityMessage, RadioContact } from './community.ts';
export type ThreadMessage = CommunityMessage & { box: 'inbox' | 'outbox' };
export type MessageFolder = 'inbox' | 'outbox' | 'sent';
export function messageIdentity(message: CommunityMessage) {
  // Current service projects authenticated peer chain/key here, never a contact label.
  const match = /^(?:(.+):)?([0-9a-f]{64})$/.exec(message.peer_name);
  return match ? { key: match[2], network: match[1] || '', id: `${match[1] || ''}:${match[2]}` } : null;
}
export function messageThreads(items: ThreadMessage[], folder: MessageFolder, search = '') {
  const groups = new Map<string, ThreadMessage[]>();
  for (const message of items) {
    const id = messageIdentity(message)?.id || `unresolved:${message.id}`;
    groups.set(id, [...(groups.get(id) || []), message]);
  }
  return [...groups].map(([id, messages]) => ({ id, identity: messageIdentity(messages[0]),
    messages: messages.sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at)) }))
    .filter((thread) => thread.messages.some((message) => folder === 'inbox' ? message.box === 'inbox'
      : message.box === 'outbox' && (folder === 'sent' ? ['sent', 'delivered', 'read'].includes(message.state) : ['queued', 'unknown', 'failed'].includes(message.state))))
    .filter((thread) => `${thread.id} ${thread.messages.map((message) => message.text).join(' ')}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()))
    .sort((a, b) => (a.identity?.key || a.id).localeCompare(b.identity?.key || b.id) || a.id.localeCompare(b.id));
}
export function replyContact(message: CommunityMessage, contacts: RadioContact[]) {
  const identity = messageIdentity(message);
  if (!identity) return null;
  const matches = contacts.filter((contact) => contact.agent_id === identity.key && contact.chain_id === identity.network);
  return matches.length === 1 ? matches[0] : null;
}
