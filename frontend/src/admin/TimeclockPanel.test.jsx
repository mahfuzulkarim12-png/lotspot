import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import TimeclockPanel from './TimeclockPanel';

const EMPLOYEES = [
  { id: 1, name: 'Jamie Rivera', created_at: '2026-07-01T08:00:00' },
  { id: 2, name: 'Morgan Lee', created_at: '2026-07-01T08:00:00' },
];

const listEmployees = vi.fn();
const timeclockStatus = vi.fn();
const clockIn = vi.fn();
const clockOut = vi.fn();
const createEmployee = vi.fn();

vi.mock('../api/client', () => ({
  api: {
    listEmployees: (...args) => listEmployees(...args),
    timeclockStatus: (...args) => timeclockStatus(...args),
    clockIn: (...args) => clockIn(...args),
    clockOut: (...args) => clockOut(...args),
    createEmployee: (...args) => createEmployee(...args),
  },
}));

vi.mock('../hooks/useTimeclockEvents', () => ({
  useTimeclockEvents: () => true,
}));

describe('TimeclockPanel', () => {
  beforeEach(() => {
    listEmployees.mockReset();
    timeclockStatus.mockReset();
    clockIn.mockReset();
    clockOut.mockReset();
    createEmployee.mockReset();

    listEmployees.mockResolvedValue(EMPLOYEES);
    timeclockStatus.mockResolvedValue({ open_shifts: [] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('clocks an employee in with a PIN', async () => {
    clockIn.mockResolvedValue({
      id: 10,
      employee_id: 1,
      employee_name: 'Jamie Rivera',
      clock_in_at: '2026-07-12T09:00:00',
      clock_out_at: null,
    });

    render(<TimeclockPanel />);

    expect(await screen.findByRole('option', { name: 'Jamie Rivera' })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Employee'), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText('PIN'), { target: { value: '4321' } });
    fireEvent.click(screen.getByRole('button', { name: 'Clock in' }));

    await waitFor(() => {
      expect(clockIn).toHaveBeenCalledWith(1, '4321');
    });
    expect(await screen.findByText('Jamie Rivera clocked in at 09:00.')).toBeInTheDocument();
  });

  test('shows Clock out for an employee with an open shift and submits clock-out', async () => {
    timeclockStatus.mockResolvedValue({
      open_shifts: [
        {
          id: 10,
          employee_id: 1,
          employee_name: 'Jamie Rivera',
          clock_in_at: '2026-07-12T09:00:00',
          clock_out_at: null,
        },
      ],
    });
    clockOut.mockResolvedValue({
      id: 10,
      employee_id: 1,
      employee_name: 'Jamie Rivera',
      clock_in_at: '2026-07-12T09:00:00',
      clock_out_at: '2026-07-12T17:00:00',
      duration_minutes: 480,
    });

    render(<TimeclockPanel />);

    // one match in the "currently clocked in" table, one in the employee <select>
    expect(await screen.findAllByText('Jamie Rivera')).toHaveLength(2);

    fireEvent.change(screen.getByLabelText('Employee'), { target: { value: '1' } });
    expect(screen.getByRole('button', { name: 'Clock out' })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('PIN'), { target: { value: '4321' } });
    fireEvent.click(screen.getByRole('button', { name: 'Clock out' }));

    await waitFor(() => {
      expect(clockOut).toHaveBeenCalledWith(1, '4321');
    });
    expect(await screen.findByText('Jamie Rivera clocked out at 17:00.')).toBeInTheDocument();
  });

  test('requires an employee and a PIN before submitting', async () => {
    render(<TimeclockPanel />);
    await screen.findByRole('option', { name: 'Jamie Rivera' });

    fireEvent.click(screen.getByRole('button', { name: 'Clock in' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Select an employee.');
    expect(clockIn).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Employee'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Clock in' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Enter a PIN.');
    expect(clockIn).not.toHaveBeenCalled();
  });

  test('adds a new employee', async () => {
    createEmployee.mockResolvedValue({ id: 3, name: 'Casey Park', created_at: '2026-07-12T09:00:00' });

    render(<TimeclockPanel />);
    await screen.findByRole('option', { name: 'Jamie Rivera' });

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Casey Park' } });
    fireEvent.change(screen.getByLabelText('New PIN'), { target: { value: '9999' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add employee' }));

    await waitFor(() => {
      expect(createEmployee).toHaveBeenCalledWith({ name: 'Casey Park', pin: '9999' });
    });
  });
});
