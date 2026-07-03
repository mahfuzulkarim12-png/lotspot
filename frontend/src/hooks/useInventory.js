import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { applyInventoryEvent } from '../lib/inventory';

/**
 * Live product list: initial fetch + SSE-driven updates.
 * The server sends a {type:'connected'} event on every (re)connect, which we
 * use to resync the full list — so a dropped connection self-heals.
 */
export function useInventory() {
  const [products, setProducts] = useState(null); // null = loading
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(null);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;

    const resync = () => {
      api
        .listProducts()
        .then((list) => {
          if (cancelledRef.current) return;
          setProducts(list);
          setError(null);
        })
        .catch((err) => {
          if (!cancelledRef.current) setError(err.message);
        });
    };

    resync();

    const source = new EventSource('/api/events');
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (message) => {
      const event = JSON.parse(message.data);
      if (event.type === 'connected') {
        setConnected(true);
        resync();
        return;
      }
      if (event.type === 'inventory') {
        setProducts((prev) => applyInventoryEvent(prev, event));
      }
    };

    return () => {
      cancelledRef.current = true;
      source.close();
    };
  }, []);

  return { products, connected, error };
}
