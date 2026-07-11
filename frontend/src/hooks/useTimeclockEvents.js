import { useEffect, useRef, useState } from 'react';

/**
 * SSE listener that only cares about timeclock events.
 * Panels use this to refetch their own data after a clock-in/clock-out.
 */
export function useTimeclockEvents(onTimeclock) {
  const [connected, setConnected] = useState(false);
  const handlerRef = useRef(onTimeclock);

  handlerRef.current = onTimeclock;

  useEffect(() => {
    const source = new EventSource('/api/events');
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (message) => {
      const event = JSON.parse(message.data);
      if (event.type === 'timeclock') {
        handlerRef.current?.(event);
      }
    };

    return () => {
      source.close();
    };
  }, []);

  return connected;
}
