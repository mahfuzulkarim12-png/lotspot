import { describe, expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import ProductGrid from './ProductGrid';

const PRODUCTS = [
  { id: 1, sku: 'COKE-330', name: 'Coca-Cola 330ml', qty: 24, price_cents: 250 },
  { id: 2, sku: 'BREAD-01', name: 'White Bread', qty: 2, price_cents: 400 },
];

describe('ProductGrid', () => {
  test('renders name, price, quantity and stock badge per product', () => {
    render(<ProductGrid products={PRODUCTS} emptyNote="nothing" />);

    expect(screen.getByText('Coca-Cola 330ml')).toBeInTheDocument();
    expect(screen.getByText('$2.50')).toBeInTheDocument();
    expect(screen.getByText('24 left')).toBeInTheDocument();
    expect(screen.getByText('In stock')).toBeInTheDocument();

    // qty 2 is at/below the low-stock threshold
    expect(screen.getByText('White Bread')).toBeInTheDocument();
    expect(screen.getByText('Low stock')).toBeInTheDocument();
  });

  test('shows the empty note when there is nothing to display', () => {
    render(<ProductGrid products={[]} emptyNote="The shelf is empty right now." />);
    expect(screen.getByText('The shelf is empty right now.')).toBeInTheDocument();
  });
});
