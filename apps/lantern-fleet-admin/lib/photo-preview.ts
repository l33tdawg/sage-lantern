export type PhotoAttachment = { image: string; preview: string };
export type SessionPhoto = PhotoAttachment & { sessionId: string };

export function photoForSession(photo: SessionPhoto | undefined, sessionId?: string): PhotoAttachment | undefined {
  return photo && sessionId === photo.sessionId ? photo : undefined;
}
