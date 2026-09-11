export type TemperatureSample = { at: number; celsius: number | null };

export function appendTemperature(samples: TemperatureSample[], at: number, millicelsius: number | null): TemperatureSample[] {
  if (!Number.isFinite(at) || (samples.length && at - samples[samples.length - 1].at < 5000)) return samples;
  const celsius = millicelsius != null && Number.isFinite(millicelsius) ? millicelsius / 1000 : null;
  return [...samples, { at, celsius }].slice(-24);
}

export function storagePercent(free?: number, total?: number): number | null {
  if (free == null || total == null || !Number.isFinite(free) || !Number.isFinite(total) || total <= 0 || free < 0 || free > total) return null;
  return 100 * (total - free) / total;
}

export function temperatureSegments(samples: TemperatureSample[]): string[] {
  if (samples.length < 2) return [];
  const start = samples[0].at;
  const span = Math.max(1000, samples[samples.length - 1].at - start);
  const segments: string[] = [];
  let points: string[] = [];
  samples.forEach((sample, index) => {
    if (sample.celsius == null || (index && sample.at - samples[index - 1].at > 15000)) {
      if (points.length > 1) segments.push(points.join(' '));
      points = [];
    }
    if (sample.celsius != null) points.push(`${10 + 280 * (sample.at - start) / span},${90 - Math.max(0, Math.min(100, sample.celsius)) * .8}`);
  });
  if (points.length > 1) segments.push(points.join(' '));
  return segments;
}
