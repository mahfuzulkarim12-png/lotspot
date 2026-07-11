// Detects hardware barcode-scanner input ("keyboard wedge") inside a text
// field that also accepts normal manual typing. Scanners fire a whole
// barcode as a burst of keydown events a few milliseconds apart, terminated
// by Enter or Tab — far faster than even a quick human typist (~100ms+ per
// keystroke). A gap wider than SCAN_MAX_INTERVAL_MS breaks the burst, so a
// paused/manual sequence never accumulates into a false scan.

/** Widest gap (ms) between keystrokes still considered part of one scan burst. */
export const SCAN_MAX_INTERVAL_MS = 80;

/** Minimum burst length before Enter/Tab is treated as a completed scan. */
export const SCAN_MIN_LENGTH = 3;

export function createScanBuffer() {
  return { chars: [], lastTimestamp: null };
}

/** Feed one printable keystroke into the buffer at `timestamp` (ms). */
export function pushScanChar(buffer, char, timestamp) {
  const isContinuous =
    buffer.lastTimestamp !== null && timestamp - buffer.lastTimestamp <= SCAN_MAX_INTERVAL_MS;
  return {
    chars: isContinuous ? [...buffer.chars, char] : [char],
    lastTimestamp: timestamp,
  };
}

export function isScanComplete(buffer) {
  return buffer.chars.length >= SCAN_MIN_LENGTH;
}

export function scanBufferCode(buffer) {
  return buffer.chars.join('');
}
