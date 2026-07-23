import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import PosPanel from './PosPanel';

const PRODUCTS = [
  { id: 1, sku: 'COKE-330', name: 'Coca-Cola 330ml', qty: 5, price_cents: 250, tax_category_id: 10 },
  { id: 2, sku: 'BREAD-01', name: 'White Bread', qty: 2, price_cents: 400, tax_category_id: null },
];

const posCheckout = vi.fn();
const playScanTone = vi.fn();
const listTaxCategories = vi.fn();
const listTaxAccounts = vi.fn();

vi.mock('react-router-dom', () => ({
  useOutletContext: () => ({ products: PRODUCTS, connected: true }),
}));

vi.mock('../api/client', () => ({
  api: {
    posCheckout: (...args) => posCheckout(...args),
    listTaxCategories: (...args) => listTaxCategories(...args),
    listTaxAccounts: (...args) => listTaxAccounts(...args),
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
    listTaxCategories.mockReset();
    listTaxAccounts.mockReset();
    posCheckout.mockResolvedValue({
      transaction_id: 'tx-12345678',
      cashier: 'admin',
      payment_method: 'cash',
      item_count: 1,
      total_qty: 2,
      total_cents: 500,
      subtotal_cents: 500,
      tax_cents: 0,
      grand_total_cents: 500,
      tax_breakdown: [],
    });
    listTaxCategories.mockResolvedValue([]);
    listTaxAccounts.mockResolvedValue([]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('builds a cart, edits quantity, and submits a bulk checkout payload', async () => {
    render(<PosPanel />);

    fireEvent.click(screen.getByRole('button', { name: /Coca-Cola 330ml/i }));

    expect(screen.getAllByText('Coca-Cola 330ml')).toHaveLength(3);
    expect(screen.getByDisplayValue('1')).toBeInTheDocument();
    // Quick-select card, search-results card, cart line "each" price, plus
    // the Subtotal and Total rows (Total == Subtotal here since tax is $0).
    expect(screen.getAllByText('$2.50')).toHaveLength(5);

    fireEvent.change(screen.getByLabelText('Qty'), { target: { value: '2' } });
    expect(screen.getByDisplayValue('2')).toBeInTheDocument();
    expect(screen.getAllByText('$5.00')).toHaveLength(3);

    fireEvent.click(screen.getByRole('button', { name: /Complete checkout/i }));

    await waitFor(() => {
      expect(posCheckout).toHaveBeenCalledWith({
        payment_method: 'cash',
        items: [{ product_id: 1, qty: 2 }],
      });
    });
  });

  test('cart renders distinct Subtotal, Tax, and Total values once tax rates load', async () => {
    listTaxCategories.mockResolvedValue([{ id: 10, name: 'Taxed', tax_account_ids: [1] }]);
    listTaxAccounts.mockResolvedValue([
      {
        id: 1,
        name: 'State Tax',
        jurisdiction: 'OK State',
        rate_bps: 1000,
        effective_from: '2000-01-01',
        effective_to: null,
      },
    ]);

    render(<PosPanel />);
    fireEvent.click(screen.getByRole('button', { name: /Coca-Cola 330ml/i }));

    // $2.50 subtotal * 10% = $0.25 tax exactly -> $2.75 total.
    await waitFor(() => {
      expect(screen.getByText('Tax').closest('.pos-total-row')).toHaveTextContent('$0.25');
    });
    expect(screen.getByText('Subtotal').closest('.pos-total-row')).toHaveTextContent('$2.50');
    expect(screen.getByText('Total').closest('.pos-total-row')).toHaveTextContent('$2.75');
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
