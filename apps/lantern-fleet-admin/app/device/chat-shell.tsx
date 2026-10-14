'use client';

/**
 * Chat's own title bar.
 *
 * Chat is a top-level destination reached from Home, so the reference gives it a
 * titled header and a conversation-list control rather than a Back it does not
 * need — the header's Home control already returns there, and a second Back
 * would be the same command twice.
 *
 * This component owns the bar and nothing else. The message stack, the composer,
 * the review and correction flows and the conversation drawer all stay exactly
 * where they were: the brief requires the current correction/review,
 * transcript/answer and reading-state logic to be *reused*, and moving it into a
 * new shell to satisfy a filename would be a rewrite dressed as a refactor.
 */
import { ChatIcon } from './icons';
import './navigation.css';

export default function ChatShell({ children, threadTitle, onOpenList, listExpanded, listControls, busy }: {
  children: React.ReactNode;
  /** The open thread's own name, when there is one. */
  threadTitle?: string;
  onOpenList: () => void;
  listExpanded: boolean;
  /** The id of the drawer this control opens, so the relationship is declared. */
  listControls: string;
  busy: boolean;
}) {
  return <section className="device-main device-chat-shell" aria-label="Chat">
    <div className="device-page-bar">
      <div className="device-page-title">
        <ChatIcon />
        <h1>{threadTitle || 'Chat'}</h1>
      </div>
      <button
        type="button"
        className="device-page-bar-action device-chat-list-button"
        onClick={onOpenList}
        aria-controls={listControls}
        aria-expanded={listExpanded}
        disabled={busy}
      >Chats</button>
    </div>
    {children}
  </section>;
}
