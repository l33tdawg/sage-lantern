'use client';

/**
 * The persistent composer at the bottom of Chat.
 *
 * Presentational on purpose. The typed lane's admission, fences, cancellation
 * and error text all live in TalkControls, which renders this: a composer that
 * opened its own typed session would be a second writer for one lane, and the
 * two could disagree about whether a review is open.
 *
 * The field is a button, not a textarea. Typed input on this device comes from
 * the on-screen keyboard, which is owned by the review the field opens. A real
 * field here would accept characters and then discard them the moment the
 * review opened with its own text, which is a worse lie than a control that
 * says what it does.
 */
import { KeyboardIcon } from './icons';
import './chat.css';

export default function ChatComposer({ fieldLabel, pending, reason, canOpen, onOpen, keyboardRef, children }: {
  /** What the typed field offers, or null while a review owns the interaction. */
  fieldLabel: string | null;
  /** A typed review is being opened right now. */
  pending: boolean;
  /** Visible reason the field cannot open; '' when it can. */
  reason: string;
  canOpen: boolean;
  onOpen: () => void;
  /** The slot the open review's inline keyboard renders into. */
  keyboardRef?: (element: HTMLDivElement | null) => void;
  /** The voice controls that stay beside the field: the microphone and its Stop/Cancel. */
  children?: React.ReactNode;
}) {
  return <div className="device-chat-composer">
    {fieldLabel !== null && <div className="device-chat-composer-row">
      {children}
      <button type="button" className="device-chat-composer-field" disabled={!canOpen} aria-busy={pending}
        aria-describedby={reason ? 'device-chat-composer-reason' : undefined}
        onClick={onOpen}>
        <KeyboardIcon />
        <span>{pending ? 'Opening the keyboard...' : fieldLabel}</span>
      </button>
    </div>}
    {reason && <p className="device-chat-composer-reason" id="device-chat-composer-reason" role="status">{reason}</p>}
    <div className="device-chat-composer-keyboard" ref={keyboardRef} />
  </div>;
}
