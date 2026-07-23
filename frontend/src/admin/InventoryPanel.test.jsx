import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import InventoryPanel from './InventoryPanel';

const PRODUCTS = [
  { id: 1, sku: 'COKE-330', name: 'Coca-Cola 330ml', qty: 24, price_cents: 250, tax_category_id: 10 },
];

const TAX_CATEGORIES = [
  { id: 10, name: 'General Merchandise', tax_account_ids: [] },
  { id: 11, name: 'Prepared Food', tax_account_ids: [] },
];

const listTaxCategories = vi.fn();
const createProduct = vi.fn();
const updateProduct = vi.fn();

vi.mock('react-router-dom', () => ({
  useOutletContext: () => ({ products: PRODUCTS }),
}));

vi.mock('../api/client', () => ({
  api: {
    listTaxCategories: (...args) => listTaxCategories(...args),
    createProduct: (...args) => createProduct(...args),
    updateProduct: (...args) => updateProduct(...args),
    deleteProduct: vi.fn(),
  },
}));

describe('InventoryPanel', () => {
  beforeEach(() => {
    listTaxCategories.mockReset();
    createProduct.mockReset();
    updateProduct.mockReset();
    listTaxCategories.mockResolvedValue(TAX_CATEGORIES);
    createProduct.mockResolvedValue({ id: 2 });
    updateProduct.mockResolvedValue({ id: 1 });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('table shows the tax category name for each product', async () => {
    render(<InventoryPanel />);
    expect(await screen.findByRole('cell', { name: 'General Merchandise' })).toBeInTheDocument();
  });

  test('create form defaults to General Merchandise and submits tax_category_id', async () => {
    render(<InventoryPanel />);
    await screen.findByRole('option', { name: 'General Merchandise' });

    fireEvent.change(screen.getByPlaceholderText('COKE-330'), { target: { value: 'CHIP-01' } });
    fireEvent.change(screen.getByPlaceholderText('Coca-Cola 330ml'), { target: { value: 'Salt Chips' } });
    fireEvent.change(screen.getByPlaceholderText('24'), { target: { value: '10' } });
    fireEvent.change(screen.getByPlaceholderText('2.50'), { target: { value: '3.50' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add product' }));

    await waitFor(() => {
      expect(createProduct).toHaveBeenCalledWith({
        sku: 'CHIP-01',
        name: 'Salt Chips',
        qty: 10,
        price_cents: 350,
        tax_category_id: 10,
      });
    });
  });

  test('editing a product can clear its tax category to "No tax category"', async () => {
    render(<InventoryPanel />);
    await screen.findByRole('cell', { name: 'General Merchandise' });

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const row = screen.getByRole('button', { name: 'Save' }).closest('tr');
    fireEvent.change(within(row).getByLabelText('Tax category'), { target: { value: '' } });
    fireEvent.click(within(row).getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(updateProduct).toHaveBeenCalledWith(1, {
        sku: 'COKE-330',
        name: 'Coca-Cola 330ml',
        qty: 24,
        price_cents: 250,
        tax_category_id: null,
      });
    });
  });
});
