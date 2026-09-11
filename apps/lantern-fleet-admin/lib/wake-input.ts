export const WAKE_QUIET_MS = 1200;
export const WAKE_IDLE_MS = 25000;

export class WakeInputGuard {
  active = false;
  private lastInput = 0;
  private pointers = new Set<number>();
  private keys = new Set<string>();

  consume(type: string, now: number, trigger: boolean, pointerId = 0, key = ''): boolean {
    if (!this.active && !trigger) return false;
    this.active = true;
    this.lastInput = now;
    if (type === 'pointerdown') this.pointers.add(pointerId);
    if (type === 'pointerup' || type === 'pointercancel') this.pointers.delete(pointerId);
    if (type === 'keydown') this.keys.add(key);
    if (type === 'keyup') this.keys.delete(key);
    if (type === 'blur') { this.pointers.clear(); this.keys.clear(); }
    return true;
  }

  release(now: number): boolean {
    if (!this.active || this.pointers.size || this.keys.size || now - this.lastInput < WAKE_QUIET_MS) return false;
    this.active = false;
    return true;
  }
}
