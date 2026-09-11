import { MicrophoneIcon } from './icons';

export default function RecordingStatus({ reply, hint }: { reply: boolean; hint: string }) {
  return <section className="device-recording-status" role="status" aria-atomic="true">
    <span className="device-recording-mic"><MicrophoneIcon /></span>
    <div>
      <p className="device-recording-label">{reply ? 'Recording your reply' : 'Recording your question'}</p>
      <h1>Listening—speak now</h1>
      <p className="device-recording-instruction">{hint}</p>
    </div>
  </section>;
}
