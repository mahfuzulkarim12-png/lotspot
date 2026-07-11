// Shift durations arrive from the API as whole minutes.

/** 95 -> "1h 35m". Invalid input renders as "0m". */
export function formatDuration(minutes) {
  if (!Number.isFinite(minutes) || minutes < 0) return '0m';
  const whole = Math.floor(minutes);
  const hours = Math.floor(whole / 60);
  const mins = whole % 60;
  return hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
}
