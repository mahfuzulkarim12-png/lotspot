import { describe, expect, test } from 'vitest';
import {
  SCAN_MAX_INTERVAL_MS,
  SCAN_MIN_LENGTH,
  createScanBuffer,
  isScanComplete,
  pushScanChar,
  scanBufferCode,
} from './scanDetection';

function feed(buffer, chars, { start = 0, step = 5 } = {}) {
  let next = buffer;
  let t = start;
  for (const char of chars) {
    next = pushScanChar(next, char, t);
    t += step;
  }
  return next;
}

describe('scan buffer', () => {
  test('a fast burst accumulates every character', () => {
    const buffer = feed(createScanBuffer(), 'COKE-330', { step: 5 });
    expect(scanBufferCode(buffer)).toBe('COKE-330');
    expect(isScanComplete(buffer)).toBe(true);
  });

  test('a gap wider than SCAN_MAX_INTERVAL_MS resets the burst', () => {
    let buffer = feed(createScanBuffer(), 'AB', { start: 0, step: 5 });
    // Next character arrives well past the max interval — starts a fresh burst.
    buffer = pushScanChar(buffer, 'C', 5 + SCAN_MAX_INTERVAL_MS + 1);
    expect(scanBufferCode(buffer)).toBe('C');
  });

  test('a gap exactly at the threshold still counts as continuous', () => {
    let buffer = pushScanChar(createScanBuffer(), 'A', 0);
    buffer = pushScanChar(buffer, 'B', SCAN_MAX_INTERVAL_MS);
    expect(scanBufferCode(buffer)).toBe('AB');
  });

  test('bursts shorter than SCAN_MIN_LENGTH are not complete', () => {
    const chars = 'A'.repeat(SCAN_MIN_LENGTH - 1);
    const buffer = feed(createScanBuffer(), chars, { step: 5 });
    expect(isScanComplete(buffer)).toBe(false);
  });

  test('slow, human-paced keystrokes never accumulate past one character', () => {
    const buffer = feed(createScanBuffer(), 'MANUAL', { step: SCAN_MAX_INTERVAL_MS + 50 });
    expect(scanBufferCode(buffer)).toBe('L');
    expect(isScanComplete(buffer)).toBe(false);
  });
});
