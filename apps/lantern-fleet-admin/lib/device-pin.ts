export const MIN_PIN_DIGITS = 8;
export const MAX_PIN_DIGITS = 12;

export function validDevicePin(pin: string): boolean {
  return pin.length >= MIN_PIN_DIGITS && pin.length <= MAX_PIN_DIGITS && !/[^0-9]/.test(pin);
}

export function editDevicePin(pin: string, key: string): string {
  if (pin.length > MAX_PIN_DIGITS || /[^0-9]/.test(pin)) return '';
  if (key === 'clear') return '';
  if (key === 'backspace') return pin.slice(0, -1);
  if (key.length === 1 && !/[^0-9]/.test(key) && pin.length < MAX_PIN_DIGITS) return pin + key;
  return pin;
}
