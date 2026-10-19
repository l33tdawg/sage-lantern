'use client';

import { useEffect, useRef } from 'react';
/**
 * Chat: the full-screen conversation destination.
 *
 * This is the frame the agreed composition asks for - Back to the screen Chat
 * was opened from (Home when nothing displaced it), the thread's own title, the
 * conversation-list control and an explicit New chat - wrapped around the
 * exchange region screen.tsx builds from the live conversation state.
 *
 * The exchange region, the review and the reading state stay where they are.
 * They are the state machine rather than the presentation, and the brief
 * requires the existing correction/review, transcript/answer and retention
 * behaviour to be reused rather than reimplemented behind a new filename.
 *
 * The composer is not a child of this frame. It is rendered by the one
 * TalkControls instance that owns the capture lane and lives in the page
 * footer, so that moving between Home and Chat cannot unmount the owner and
 * leave a recording behind. Visually it is the bottom of this screen.
 */
import { BackIcon } from './icons';
/**
 * The page bar's own stylesheet.
 *
 * navigation.css was imported only by the shell this frame replaces, so
 * dropping that import silently stripped `.device-page-bar` from every
 * destination that carries one - Gallery and Agent included, not just Chat.
 * The frame renders a page bar, so the frame keeps the stylesheet reachable.
 */
import './navigation.css';
import './chat.css';

export default function Chat({ children, threadTitle, onBack, onOpenList, listExpanded, listControls, onNewChat, busy = false, notice }: {
  children: React.ReactNode;
  /** The open thread's own name, when there is one. */
  threadTitle?: string;
  /**
   * Return to the screen Chat was opened from, or Home when nothing displaced
   * it. Absent on a unit with no Home screen, where Chat is the root surface
   * and a Back control would have nowhere to go.
   *
   * Disabled while a review or a recording is in progress: leaving then would
   * cancel the review or discard the draft, so the frame states that instead of
   * letting the press do nothing.
   */
  onBack?: () => void;
  onOpenList: () => void;
  listExpanded: boolean;
  /** The id of the drawer this control opens, so the relationship is declared. */
  listControls: string;
  /** Run the New chat policy: clear this thread, keep saved conversations. */
  onNewChat: () => void;
  busy?: boolean;
  /**
   * Why a header control cannot act, in visible text.
   *
   * A disabled button with its reason only in `title`/`aria-label` is not a
   * reason on a touch panel, so the frame states it instead of relying on a
   * hover tooltip no finger can reach.
   */
  notice?: string;
}) {
  /**
   * Chat's entry point takes the keyboard when the surface opens.
   *
   * Arriving from the Home tile used to leave `activeElement` on the document
   * body: the *exit* was fixed to restore the tile, but nothing claimed focus on
   * the way in, so a keyboard or assistive-technology user landed nowhere. Every
   * other destination opens onto its own Back control - settings.tsx focuses
   * its Back on mount - and Chat follows that convention. On a unit with no
   * Home screen there is no Back control, so there is nothing to claim and the
   * composer keeps the keyboard.
   */
  const back = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    // Only when the keyboard would otherwise be nowhere, which is the defect:
    // arriving from the Home tile left focus on the document body. Claiming it
    // unconditionally steals it from a control the user is already using — the
    // composer's field — and the on-screen keys then write into nothing
    // (measured: pressed "hi", the draft stayed empty).
    const active = document.activeElement;
    if (active === null || active === document.body || active === document.documentElement) back.current?.focus();
  }, []);
  return <section className="device-main device-chat" aria-label="Chat">
    <div className="device-page-bar device-chat-bar">
      {onBack && <button ref={back} type="button" className="device-chat-back" onClick={onBack} disabled={busy}><BackIcon /><span>Back</span></button>}
      <div className="device-page-title"><h1>{threadTitle || 'Chat'}</h1></div>
      <button
        type="button"
        className="device-page-bar-action device-chat-list-button"
        onClick={onOpenList}
        aria-controls={listControls}
        aria-expanded={listExpanded}
        disabled={busy}
      >Chats</button>
      <button
        type="button"
        className="device-page-bar-action device-chat-new-chat"
        onClick={onNewChat}
        disabled={busy}
      >New chat</button>
    </div>
    {notice && <p className="device-chat-notice" role="status">{notice}</p>}
    {children}
  </section>;
}
