import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import PosPanel from './PosPanel';

const PRODUCTS = [
  { id: 1, sku: 'COKE-330', name: 'Coca-Cola 330ml', qty: 5, price_cents: 250 },
  { id: 2, sku: 'BREAD-01', name: 'White Bread', qty: 2, price_cents: 400 },
];

const posCheckout = vi.fn();

vi.mock('react-router-dom', () => ({
  useOutletContext: () => ({ products: PRODUCTS, connected: true }),
}));

vi.mock('../api/client', () => ({
  api: {
    posCheckout: (...args) => posCheckout(...args),
  },
}));

describe('PosPanel', () => {
  beforeEach(() => {
    posCheckout.mockReset();
    posCheckout.mockResolvedValue({
      transaction_id: 'tx-12345678',
      cashier: 'admin',
      payment_method: 'cash',
      item_count: 1,
      total_qty: 2,
      total_cents: 500,
    });
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
});
