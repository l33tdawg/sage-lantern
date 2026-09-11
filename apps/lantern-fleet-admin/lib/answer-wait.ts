export const WAIT_PHRASE_INTERVAL_MS = 3000;

export function workingMessages(stage?: string | null): readonly string[] {
  switch (stage) {
    case 'starting': return ['Getting things ready…', 'Getting started…', 'Taking a moment to get ready…'];
    case 'transcribing': return ['Making sense of your question…', 'Turning your recording into words…', 'Working through your recording…'];
    case 'recalling': return ['Checking on that…', 'Looking for helpful context…', 'Bringing the details together…'];
    case 'photo_analysis': return ['Taking a look…', 'Looking at your photo…', 'Working through the details…'];
    case 'generating': return ['Getting things together…', 'Thinking it through…', 'Working on your question…', 'Putting a response together…'];
    case 'answering': return ['Putting it into words…', 'Writing your answer…', 'Working through the details…'];
    case 'speaking': return ['Getting ready to speak…', 'Preparing the audio…', 'Working on the spoken answer…'];
    default: return ['Working on that…', 'Working on your question…', 'Taking a moment…'];
  }
}

export function workingMessage(stage?: string | null): string {
  return workingMessages(stage)[0];
}
