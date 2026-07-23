import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import TaxSettingsPanel from './TaxSettingsPanel';

const ACCOUNTS = [
  {
    id: 1,
    name: 'OK State',
    jurisdiction: 'Oklahoma',
    rate_bps: 450,
    effective_from: '2026-01-01',
    effective_to: null,
  },
];

const CATEGORIES = [
  { id: 10, name: 'General Merchandise', tax_account_ids: [] },
  { id: 11, name: 'Prepared Food', tax_account_ids: [1] },
];

const listTaxAccounts = vi.fn();
const listTaxCategories = vi.fn();
const createTaxAccount = vi.fn();
const updateTaxAccount = vi.fn();
const deleteTaxAccount = vi.fn();
const setTaxCategoryAccounts = vi.fn();

vi.mock('../api/client', () => ({
  api: {
    listTaxAccounts: (...args) => listTaxAccounts(...args),
    listTaxCategories: (...args) => listTaxCategories(...args),
    createTaxAccount: (...args) => createTaxAccount(...args),
    updateTaxAccount: (...args) => updateTaxAccount(...args),
    deleteTaxAccount: (...args) => deleteTaxAccount(...args),
    setTaxCategoryAccounts: (...args) => setTaxCategoryAccounts(...args),
  },
}));

describe('TaxSettingsPanel', () => {
  beforeEach(() => {
    listTaxAccounts.mockReset();
    listTaxCategories.mockReset();
    createTaxAccount.mockReset();
    updateTaxAccount.mockReset();
    deleteTaxAccount.mockReset();
    setTaxCategoryAccounts.mockReset();

    listTaxAccounts.mockResolvedValue(ACCOUNTS);
    listTaxCategories.mockResolvedValue(CATEGORIES);
    createTaxAccount.mockResolvedValue({ id: 2 });
    setTaxCategoryAccounts.mockResolvedValue({ ...CATEGORIES[0], tax_account_ids: [1] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders existing tax accounts and the category mapping matrix', async () => {
    render(<TaxSettingsPanel />);

    expect(await screen.findByRole('cell', { name: 'OK State' })).toBeInTheDocument();
    expect(screen.getByText('4.5%')).toBeInTheDocument();
    expect(screen.getByText('General Merchandise')).toBeInTheDocument();

    const preparedFoodRow = screen.getByText('Prepared Food').closest('tr');
    expect(within(preparedFoodRow).getByRole('checkbox')).toBeChecked();

    const generalRow = screen.getByText('General Merchandise').closest('tr');
    expect(within(generalRow).getByRole('checkbox')).not.toBeChecked();
  });

  test('adding a tax account converts a percent rate into basis points', async () => {
    render(<TaxSettingsPanel />);
    await screen.findByRole('cell', { name: 'OK State' });

    fireEvent.change(screen.getByPlaceholderText('OK State'), { target: { value: 'Tulsa City' } });
    fireEvent.change(screen.getByPlaceholderText('Oklahoma'), { target: { value: 'Tulsa' } });
    fireEvent.change(screen.getByPlaceholderText('4.50'), { target: { value: '2' } });
    fireEvent.change(screen.getByLabelText('Effective from'), { target: { value: '2026-01-01' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add tax account' }));

    await waitFor(() => {
      expect(createTaxAccount).toHaveBeenCalledWith({
        name: 'Tulsa City',
        jurisdiction: 'Tulsa',
        rate_bps: 200,
        effective_from: '2026-01-01',
        effective_to: null,
      });
    });
  });

  test('toggling a checkbox replaces the category tax_account_ids', async () => {
    render(<TaxSettingsPanel />);
    await screen.findByRole('cell', { name: 'OK State' });

    const generalRow = screen.getByText('General Merchandise').closest('tr');
    fireEvent.click(within(generalRow).getByRole('checkbox'));

    await waitFor(() => {
      expect(setTaxCategoryAccounts).toHaveBeenCalledWith(10, [1]);
    });
  });

  test('deletes a tax account after confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    deleteTaxAccount.mockResolvedValue({ deleted: 1 });

    render(<TaxSettingsPanel />);
    await screen.findByRole('cell', { name: 'OK State' });

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => {
      expect(deleteTaxAccount).toHaveBeenCalledWith(1);
    });
  });
});
