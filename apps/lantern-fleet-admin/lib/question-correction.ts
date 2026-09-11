export function correctionTextValid(text: string): boolean {
  return text.trim().length > 0 && new TextEncoder().encode(text).length <= 4096
    && ![...text].some((character) => { const point = character.codePointAt(0)!; return point >= 0xd800 && point <= 0xdfff; })
    && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(text);
}

export class CorrectionAttempts {
  private readonly pending = new Set<string>();

  blocked(sourceId: string): boolean {
    return this.pending.has(sourceId);
  }

  begin(sourceId: string): boolean {
    if (this.blocked(sourceId)) return false;
    this.pending.add(sourceId);
    return true;
  }

  confirmed(sourceId: string): void {
    this.pending.delete(sourceId);
  }
}
