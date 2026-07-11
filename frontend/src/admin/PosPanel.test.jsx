import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import PosPanel from './PosPanel';

const PRODUCTS = [
  { id: 1, sku: 'COKE-330', name: 'Coca-Cola 330ml', qty: 5, price_cents: 250 },
  { id: 2, sku: 'BREAD-01', name: 'White Bread', qty: 2, price_cents: 400 },
];

const posCheckout = vi.fn();
const playScanTone = vi.fn();

vi.mock('react-router-dom', () => ({
  useOutletContext: () => ({ products: PRODUCTS, connected: true }),
}));

vi.mock('../api/client', () => ({
  api: {
    posCheckout: (...args) => posCheckout(...args),
  },
}));

vi.mock('../lib/scanAudio', () => ({
  playScanTone: (...args) => playScanTone(...args),
}));

/**
 * Fires one keydown per character `stepMs` apart (mocking Date.now so the
 * component's own scan-burst timer sees exactly that gap), then a
 * terminating Enter/Tab keydown — the same shape a keyboard-wedge barcode
 * scanner produces.
 */
function fireScan(input, code, { stepMs = 5, terminator = 'Enter' } = {}) {
  const clock = vi.spyOn(Date, 'now').mockReturnValue(0);
  let elapsed = 0;
  for (const char of code) {
    elapsed += stepMs;
    clock.mockReturnValue(elapsed);
    fireEvent.keyDown(input, { key: char });
    fireEvent.change(input, { target: { value: input.value + char } });
  }
  elapsed += stepMs;
  clock.mockReturnValue(elapsed);
  fireEvent.keyDown(input, { key: terminator });
  clock.mockRestore();
}

describe('PosPanel', () => {
  beforeEach(() => {
    posCheckout.mockReset();
    playScanTone.mockReset();
    posCheckout.mockResolvedValue({
      transaction_id: 'tx-12345678',
      cashier: 'admin',
      payment_method: 'cash',
      item_count: 1,
      total_qty: 2,
      total_cents: 500,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('builds a cart, edits quantity, and submits a bulk checkout payload', async () => {
    render(<PosPanel />);

    fireEvent.click(screen.getByRole('button', { name: /Coca-Cola 330ml/i }));

    expect(screen.getAllByText('Coca-Cola 330ml')).toHaveLength(3);
    expect(screen.getByDisplayValue('1')).toBeInTheDocument();
    expect(screen.getAllByText('$2.50')).toHaveLength(4);

    fireEvent.change(screen.getByLabelText('Qty'), { target: { value: '2' } });
    expect(screen.getByDisplayValue('2')).toBeInTheDocument();
    expect(screen.getAllByText('$5.00')).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: /Complete checkout/i }));

    await waitFor(() => {
      expect(posCheckout).toHaveBeenCalledWith({
        payment_method: 'cash',
        items: [{ product_id: 1, qty: 2 }],
      });
    });
  });

  describe('barcode scanner input', () => {
    test('a fast keystroke burst ending in Enter adds the matching SKU to the cart', () => {
      render(<PosPanel />);
      const input = screen.getByLabelText('Search products');

      fireScan(input, 'COKE-330');

      expect(screen.getByText('Scanned Coca-Cola 330ml — added to cart.')).toBeInTheDocument();
      expect(screen.getByDisplayValue('1')).toBeInTheDocument();
      expect(playScanTone).toHaveBeenCalledWith('success');
      expect(input).toHaveValue('');
    });

    test('scanning the same SKU twice increments the cart quantity instead of duplicating it', () => {
      render(<PosPanel />);
      const input = screen.getByLabelText('Search products');

      fireScan(input, 'COKE-330');
      fireScan(input, 'COKE-330');

      expect(screen.getByDisplayValue('2')).toBeInTheDocument();
    });

    test('an unknown SKU scan shows an error and leaves the cart untouched', () => {
      render(<PosPanel />);
      const input = screen.getByLabelText('Search products');

      fireScan(input, 'NOPE-404');

      expect(screen.getByRole('alert')).toHaveTextContent('No product matches scanned code “NOPE-404”.');
      expect(playScanTone).toHaveBeenCalledWith('error');
      expect(
        screen.getByText('Add products from the search results or quick-select grid.')
      ).toBeInTheDocument();
    });

    test('slow, human-paced keystrokes are never treated as a scan', () => {
      render(<PosPanel />);
      const input = screen.getByLabelText('Search products');

      fireScan(input, 'COKE-330', { stepMs: 150 });

      expect(screen.queryByText(/^Scanned/)).not.toBeInTheDocument();
      expect(playScanTone).not.toHaveBeenCalled();
      expect(
        screen.getByText('Add products from the search results or quick-select grid.')
      ).toBeInTheDocument();
    });

    test('manual substring search still filters results unaffected by scan detection', () => {
      render(<PosPanel />);
      const input = screen.getByLabelText('Search products');

      fireEvent.change(input, { target: { value: 'bread' } });

      expect(screen.getByText('1 live products found.')).toBeInTheDocument();
      expect(screen.getAllByText('White Bread').length).toBeGreaterThan(0);
    });
  });
});
