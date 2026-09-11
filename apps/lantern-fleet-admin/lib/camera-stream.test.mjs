import assert from 'node:assert/strict';
import test from 'node:test';
import { CameraFrameDecoder, MAX_CAMERA_FRAME_BYTES } from './camera-stream.ts';

const jpeg = Uint8Array.from([255, 216, 1, 2, 3, 255, 217]);
function packet(bytes = jpeg) {
  const data = new Uint8Array(bytes.length + 4);
  new DataView(data.buffer).setUint32(0, bytes.length);
  data.set(bytes, 4);
  return data;
}

test('camera frames survive every split boundary', () => {
  const data = packet();
  for (let split = 0; split <= data.length; split++) {
    const decoder = new CameraFrameDecoder();
    const frames = [...decoder.push(data.slice(0, split)), ...decoder.push(data.slice(split))];
    assert.deepEqual(frames, [jpeg]);
    decoder.finish();
  }
});

test('multiple and bytewise frames are supported', () => {
  const data = Uint8Array.from([...packet(), ...packet()]);
  const decoder = new CameraFrameDecoder();
  assert.deepEqual(decoder.push(data), [jpeg, jpeg]);
  decoder.finish();
  const fragmented = new CameraFrameDecoder();
  const frames = [...data].flatMap((byte) => fragmented.push(Uint8Array.of(byte)));
  assert.deepEqual(frames, [jpeg, jpeg]);
  fragmented.finish();
});

test('invalid lengths are rejected before frame allocation', () => {
  for (const size of [0, 3, MAX_CAMERA_FRAME_BYTES + 1, 0xffffffff]) {
    const header = new Uint8Array(4);
    new DataView(header.buffer).setUint32(0, size);
    assert.throws(() => new CameraFrameDecoder().push(header), /size/);
  }
});

test('malformed and truncated images do not become previews', () => {
  assert.throws(() => new CameraFrameDecoder().push(packet(Uint8Array.of(1, 2, 3, 4))), /image/);
  for (const count of [1, 3, 4, 8]) {
    const decoder = new CameraFrameDecoder();
    decoder.push(packet().slice(0, count));
    assert.throws(() => decoder.finish(), /Incomplete/);
  }
});
