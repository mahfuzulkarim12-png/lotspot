// Best-effort audio feedback for barcode scans via the Web Audio API.
// Scanning must keep working even where audio is unavailable or blocked
// (no AudioContext in the test environment, autoplay policies, etc.), so
// every failure mode here is swallowed rather than surfaced.

const TONE_GAIN = 0.15;
const BEEP_SECONDS = 0.12;
const GAP_SECONDS = 0.06;

let sharedContext;

function getAudioContext() {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) return null;
  if (!sharedContext) sharedContext = new AudioContextClass();
  return sharedContext;
}

function beep(context, frequency, startAt) {
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  oscillator.frequency.value = frequency;
  oscillator.connect(gain);
  gain.connect(context.destination);
  gain.gain.setValueAtTime(TONE_GAIN, startAt);
  oscillator.start(startAt);
  oscillator.stop(startAt + BEEP_SECONDS);
}

/** One high beep for a matched scan, two low beeps for an unmatched/failed scan. */
export function playScanTone(kind) {
  try {
    const context = getAudioContext();
    if (!context) return;
    const now = context.currentTime;
    if (kind === 'success') {
      beep(context, 880, now);
      return;
    }
    beep(context, 220, now);
    beep(context, 220, now + BEEP_SECONDS + GAP_SECONDS);
  } catch {
    // Audio feedback is a nice-to-have; never let it break scanning.
  }
}
