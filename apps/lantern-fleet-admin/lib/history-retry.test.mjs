import assert from 'node:assert/strict';
import test from 'node:test';
import { prepareHistoryPhoto, retryPhotoSize } from './history-retry.ts';

test('saved photo inference is bounded without enlarging originals', () => {
  assert.deepEqual(retryPhotoSize(1920, 1080), { width: 256, height: 144 });
  assert.deepEqual(retryPhotoSize(640, 480), { width: 256, height: 192 });
  assert.deepEqual(retryPhotoSize(100, 100), { width: 100, height: 100 });
  for (const dimensions of [[0, 480], [1921, 1080], [640, 1081], [NaN, 20], [1.5, 3]]) assert.throws(() => retryPhotoSize(...dimensions));
});

test('saved photo keeps original while generating a new inference derivative', async () => {
  const originalImage = globalThis.Image;
  const originalDocument = globalThis.document;
  const canvas = { width: 0, height: 0, getContext: () => ({ drawImage() {} }), toDataURL: () => 'data:image/jpeg;base64,/9j/NEW=' };
  try {
    globalThis.Image = class { naturalWidth = 640; naturalHeight = 480; async decode() {} };
    globalThis.document = { createElement: () => canvas };
    assert.deepEqual(await prepareHistoryPhoto('data:image/jpeg;base64,/9j/OLD='), { image: '/9j/NEW=', preview: '/9j/OLD=' });
    assert.equal(canvas.width, 256);
    assert.equal(canvas.height, 192);
    globalThis.Image = class { async decode() { throw new Error('corrupt'); } };
    await assert.rejects(prepareHistoryPhoto('data:image/jpeg;base64,/9j/OLD='));
    await assert.rejects(prepareHistoryPhoto('https://example.com/photo.jpg'));
  } finally {
    if (originalImage === undefined) delete globalThis.Image; else globalThis.Image = originalImage;
    if (originalDocument === undefined) delete globalThis.document; else globalThis.document = originalDocument;
  }
});
