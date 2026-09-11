import type { RadioContact } from './community.ts';
import type { RadioHealth } from './radio-status.ts';

export function heardContactAge(contact: RadioContact, contacts: RadioContact[], nodes: RadioHealth['discovered_nodes'], elapsedSeconds: number, unixSeconds: number): number | null {
  if (!Number.isFinite(elapsedSeconds) || elapsedSeconds < 0 || !Number.isFinite(unixSeconds)
    || contact.expires_at <= unixSeconds
    || contacts.filter((entry) => entry.radio_public_key === contact.radio_public_key && entry.expires_at > unixSeconds).length !== 1) return null;
  const matches = nodes.filter((node) => node.public_key === contact.radio_public_key);
  if (matches.length !== 1) return null;
  const age = matches[0].age_seconds + elapsedSeconds;
  return age >= 0 && age < 300 ? age : null;
}
