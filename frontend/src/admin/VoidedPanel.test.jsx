import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import VoidedPanel from './VoidedPanel';

const RECEIPTS = [
  {
    transaction_id: 'abc12345ffffffff',
    sold_at: '2026-07-20T10:00:00',
    cashier: 'admin',
    payment_method: 'cash',
    voided_at: '2026-07-20T10:05:00',
    subtotal_cents: 500,
    tax_cents: 25,
    grand_total_cents: 525,
    item_count: 1,
    line_items: [
      {
        id: 1,
        product_name: 'Coca-Cola 330ml',
        sku: 'COKE-330',
        qty: 2,
        unit_price_cents: 250,
        total_cents: 500,
      },
    ],
  },
];

const ITEMS = [
  {
    id: 5,
    transaction_id: 'def67890ffffffff',
    sold_at: '2026-07-21T11:00:00',
    voided_at: '2026-07-21T11:10:00',
    product_name: 'Chips',
    sku: 'CHIPS',
    qty: 1,
    unit_price_cents: 175,
    total_cents: 175,
    void_reason: 'Damaged',
  },
];

const voidedReceipts = vi.fn();
const voidedItems = vi.fn();

vi.mock('../api/client', () => ({
  api: {
    voidedReceipts: (...args) => voidedReceipts(...args),
    voidedItems: (...args) => voidedItems(...args),
  },
}));

vi.mock('../hooks/useSaleEvents', () => ({
  useSaleEvents: () => true,
}));

describe('VoidedPanel', () => {
  beforeEach(() => {
    voidedReceipts.mockReset();
    voidedItems.mockReset();
    voidedReceipts.mockResolvedValue(RECEIPTS);
    voidedItems.mockResolvedValue(ITEMS);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders a voided receipt row collapsed, expanding reveals its line items', async () => {
    render(<VoidedPanel />);

    expect(await screen.findByText('abc12345')).toBeInTheDocument();
    expect(screen.queryByText('Coca-Cola 330ml')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Expand line items' }));

    expect(await screen.findByText('Coca-Cola 330ml')).toBeInTheDocument();
    expect(screen.getByText('COKE-330')).toBeInTheDocument();
  });

  test('renders a voided item row with its reason and receipt id', async () => {
    render(<VoidedPanel />);

    expect(await screen.findByText('Chips')).toBeInTheDocument();
    expect(screen.getByText('Damaged')).toBeInTheDocument();
    expect(screen.getByText('def67890')).toBeInTheDocument();
  });

  test('shows empty states when nothing is found', async () => {
    voidedReceipts.mockResolvedValue([]);
    voidedItems.mockResolvedValue([]);
    render(<VoidedPanel />);

    expect(await screen.findByText('No voided receipts found for this range.')).toBeInTheDocument();
    expect(screen.getByText('No voided items found for this range.')).toBeInTheDocument();
  });

  test('searching by receipt id re-queries both endpoints with q', async () => {
    render(<VoidedPanel />);
    await screen.findByText('abc12345');

    fireEvent.change(screen.getByLabelText('Receipt ID'), { target: { value: 'abc123' } });

    await waitFor(() => {
      expect(voidedReceipts).toHaveBeenCalledWith(expect.objectContaining({ q: 'abc123' }));
      expect(voidedItems).toHaveBeenCalledWith(expect.objectContaining({ q: 'abc123' }));
    });
  });

  test('changing the range preset re-queries with a wider start date', async () => {
    render(<VoidedPanel />);
    await screen.findByText('abc12345');
    const firstCallArgs = voidedReceipts.mock.calls.at(-1)[0];

    fireEvent.change(screen.getByLabelText('Range'), { target: { value: '30' } });

    await waitFor(() => {
      const lastCallArgs = voidedReceipts.mock.calls.at(-1)[0];
      expect(lastCallArgs.start).not.toBe(firstCallArgs.start);
    });
  });
});
