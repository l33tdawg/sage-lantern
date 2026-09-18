import { communityLabel, type CommunityMessage, type MessageState, type RadioContact } from './community.ts';
export type ThreadMessage = CommunityMessage & { box: 'inbox' | 'outbox' };
export type MessageIdentity = { key: string; network: string; id: string };
export type MessageThread = {
  id: string;
  identity: MessageIdentity | null;
  title: string;
  messages: ThreadMessage[];
  last: ThreadMessage;
  unread: number;
};
export const messageFolders = ['inbox', 'outbox'] as const;
export type MessageFolder = typeof messageFolders[number];
export type MessagePage = { items: CommunityMessage[]; more: boolean };
export type FolderLoad = { box: MessageFolder; page: MessagePage | null };

// Each folder is fetched on its own: one failing route must never hide the other's rows.
export async function loadMessageFolders(load: (box: MessageFolder) => Promise<MessagePage>): Promise<FolderLoad[]> {
  return Promise.all(messageFolders.map(async (box) => {
    try { return { box, page: await load(box) }; } catch { return { box, page: null }; }
  }));
}
export function folderItems(loads: FolderLoad[]): ThreadMessage[] {
  const items: ThreadMessage[] = [];
  for (const { box, page } of loads) if (page) for (const item of page.items) items.push({ ...item, box });
  return items;
}
export function folderUnavailable(loads: FolderLoad[]): MessageFolder[] {
  return loads.filter((load) => !load.page).map((load) => load.box);
}
export function folderNotice(box: MessageFolder): string {
  return box === 'inbox' ? 'Inbox unavailable. Try Refresh.' : 'Outbox unavailable. Try Refresh.';
}
export function messageIdentity(message: CommunityMessage): MessageIdentity | null {
  // Current service projects authenticated peer chain/key here, never a contact label.
  const match = /^(?:(.+):)?([0-9a-f]{64})$/.exec(message.peer_name);
  return match ? { key: match[2], network: match[1] || '', id: `${match[1] || ''}:${match[2]}` } : null;
}
export function contactNames(contacts: RadioContact[]): Record<string, string> {
  const names: Record<string, string> = {};
  for (const contact of contacts) names[`${contact.chain_id}:${contact.agent_id}`] = contact.name;
  return names;
}
export function threadTitle(identity: MessageIdentity | null, names: Record<string, string> = {}): string {
  if (!identity) return 'Unresolved sender';
  return names[`${identity.network}:${identity.key}`] || `${identity.key.slice(0, 10)}…${identity.key.slice(-6)}`;
}
export function messageThreads(items: ThreadMessage[], options: {
  search?: string; names?: Record<string, string>; seen?: ReadonlySet<string>;
} = {}): MessageThread[] {
  const { search = '', names = {}, seen = new Set<string>() } = options;
  const groups = new Map<string, ThreadMessage[]>();
  for (const message of items) {
    const id = messageIdentity(message)?.id || `unresolved:${message.id}`;
    groups.set(id, [...(groups.get(id) || []), message]);
  }
  return [...groups].map(([id, messages]) => {
    const sorted = [...messages].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at));
    const identity = messageIdentity(sorted[0]);
    return { id, identity, title: threadTitle(identity, names), messages: sorted,
      last: sorted[sorted.length - 1],
      unread: sorted.filter((message) => message.box === 'inbox' && !seen.has(message.id)).length };
  })
    .filter((thread) => `${thread.title} ${thread.messages.map((message) => message.text).join(' ')}`
      .toLocaleLowerCase().includes(search.toLocaleLowerCase()))
    .sort((a, b) => Date.parse(b.last.created_at) - Date.parse(a.last.created_at)
      || (a.identity?.key || a.id).localeCompare(b.identity?.key || b.id));
}
export function replyContact(message: CommunityMessage, contacts: RadioContact[]) {
  const identity = messageIdentity(message);
  if (!identity) return null;
  const matches = contacts.filter((contact) => contact.agent_id === identity.key && contact.chain_id === identity.network);
  return matches.length === 1 ? matches[0] : null;
}
export function outgoingLabel(state: MessageState): string {
  return state === 'queued' ? 'Not delivered yet' : communityLabel(state);
}
export function deliveryLabel(message: { box: 'inbox' | 'outbox'; state: MessageState }): string {
  return message.box === 'inbox' ? 'received' : outgoingLabel(message.state);
}
export function previewPrefix(message: { box: 'inbox' | 'outbox'; state: MessageState }): string {
  return message.box === 'outbox' ? `You · ${deliveryLabel(message)}: ` : '';
}
