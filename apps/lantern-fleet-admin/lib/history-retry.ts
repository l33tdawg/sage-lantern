import type { PhotoAttachment } from './photo-preview';

export function retryPhotoSize(width: number, height: number) {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 || width > 1920 || height > 1080) throw new Error('Photo unavailable');
  const scale = Math.min(1, 256 / width, 192 / height);
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

export async function prepareHistoryPhoto(source: string): Promise<PhotoAttachment> {
  if (!source.startsWith('data:image/jpeg;base64,/9j/') || source.length > 2796230) throw new Error('Photo unavailable');
  const image = new Image();
  image.src = source;
  await image.decode();
  const size = retryPhotoSize(image.naturalWidth, image.naturalHeight);
  const canvas = document.createElement('canvas');
  canvas.width = size.width;
  canvas.height = size.height;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Photo unavailable');
  context.drawImage(image, 0, 0, size.width, size.height);
  const encoded = canvas.toDataURL('image/jpeg', .8).split(',')[1];
  if (!encoded || !encoded.startsWith('/9j/')) throw new Error('Photo unavailable');
  return { image: encoded, preview: source.split(',')[1] };
}
