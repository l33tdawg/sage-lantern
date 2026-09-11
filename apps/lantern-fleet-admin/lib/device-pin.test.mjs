import assert from 'node:assert/strict';
import test from 'node:test';
import { editDevicePin, validDevicePin } from './device-pin.ts';

test('PIN accepts only eight through twelve ASCII digits without coercion', () => {
  for (const value of ['00000000', '012345678', '123456789012']) assert.equal(validDevicePin(value), true);
  for (const value of ['', '1234567', '1234567890123', '1234567a', '１２３４５６７８', '١٢٣٤٥٦٧٨', '12345678\n', ' 12345678', '1e123456']) assert.equal(validDevicePin(value), false);
});

test('keypad preserves leading zeros and bounds entry', () => {
  assert.equal(editDevicePin('0000000', '0'), '00000000');
  assert.equal(editDevicePin('123456789012', '3'), '123456789012');
  assert.equal(editDevicePin('123', '12'), '123');
  assert.equal(editDevicePin('123', 'a'), '123');
  assert.equal(editDevicePin('123', '١'), '123');
});

test('clear and backspace never retain malformed state', () => {
  assert.equal(editDevicePin('12345678', 'clear'), '');
  assert.equal(editDevicePin('12345678', 'backspace'), '1234567');
  assert.equal(editDevicePin('', 'backspace'), '');
  assert.equal(editDevicePin('malformed', '1'), '');
});
