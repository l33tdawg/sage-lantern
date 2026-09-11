import type { ReactNode } from 'react';
import RecordingStatus from './recording-status';
import QuestionContext from './question-context';
import './question-flow.css';

export default function QuestionTurn({ phase, question, answer, reply, hint, edit, review }: {
  phase: string; question: string | null; answer: string; reply: boolean; hint: string;
  edit?: () => void; review?: ReactNode;
}) {
  return <article className="device-chat-exchange device-question-turn" data-phase={phase} aria-label="Current question">
    <section className={`device-chat-user device-flow-question${review || edit ? ' device-flow-question-editable' : ''}`}>
      {phase === 'listening' ? <RecordingStatus reply={reply} hint={hint} /> : review || (question
        ? <QuestionContext transcript={question} edit={edit} label="Your question" />
        : <div className="device-transcribing-question"><h2>Your question</h2><p role="status">Turning your recording into words…</p></div>)}
    </section>
    {answer && <section className="device-chat-assistant device-flow-answer"><h2>Lantern</h2><p>{answer}</p></section>}
  </article>;
}
