import { useEffect, useRef, useState } from 'react';

/**
 * SSE listener that only cares about sale events.
 * Panels use this to refetch their own data after a successful checkout.
 */
export function useSaleEvents(onSale) {
  const [connected, setConnected] = useState(false);
  const handlerRef = useRef(onSale);

  handlerRef.current = onSale;

  useEffect(() => {
    const source = new EventSource('/api/events');
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (message) => {
      const event = JSON.parse(message.data);
      if (event.type === 'sale') {
        handlerRef.current?.(event);
      }
    };

    return () => {
      source.close();
    };
  }, []);

  return connected;
}
