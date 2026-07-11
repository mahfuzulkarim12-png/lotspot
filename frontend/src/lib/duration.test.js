import { describe, expect, test } from 'vitest';
import { formatDuration } from './duration';

describe('formatDuration', () => {
  test('formats minutes under an hour', () => {
    expect(formatDuration(0)).toBe('0m');
    expect(formatDuration(45)).toBe('45m');
  });

  test('formats hours and remaining minutes', () => {
    expect(formatDuration(60)).toBe('1h 0m');
    expect(formatDuration(95)).toBe('1h 35m');
    expect(formatDuration(510)).toBe('8h 30m');
  });

  test('renders invalid input as 0m instead of crashing', () => {
    expect(formatDuration(undefined)).toBe('0m');
    expect(formatDuration(null)).toBe('0m');
    expect(formatDuration(NaN)).toBe('0m');
    expect(formatDuration(-5)).toBe('0m');
  });
});
