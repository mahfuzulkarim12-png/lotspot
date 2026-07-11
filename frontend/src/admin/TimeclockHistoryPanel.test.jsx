import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import TimeclockHistoryPanel from './TimeclockHistoryPanel';

const EMPLOYEES = [
  { id: 1, name: 'Jamie Rivera', created_at: '2026-07-01T08:00:00' },
  { id: 2, name: 'Morgan Lee', created_at: '2026-07-01T08:00:00' },
];

const ENTRIES = [
  {
    id: 1,
    employee_id: 1,
    employee_name: 'Jamie Rivera',
    clock_in_at: '2026-07-10T09:00:00',
    clock_out_at: '2026-07-10T17:30:00',
    duration_minutes: 510,
  },
  {
    id: 2,
    employee_id: 1,
    employee_name: 'Jamie Rivera',
    clock_in_at: '2026-07-11T09:00:00',
    clock_out_at: null,
    duration_minutes: null,
  },
];

const listEmployees = vi.fn();
const timeclockHistory = vi.fn();

vi.mock('../api/client', () => ({
  api: {
    listEmployees: (...args) => listEmployees(...args),
    timeclockHistory: (...args) => timeclockHistory(...args),
  },
}));

vi.mock('../hooks/useTimeclockEvents', () => ({
  useTimeclockEvents: () => true,
}));

describe('TimeclockHistoryPanel', () => {
  beforeEach(() => {
    listEmployees.mockReset();
    timeclockHistory.mockReset();
    listEmployees.mockResolvedValue(EMPLOYEES);
    timeclockHistory.mockResolvedValue({ entries: ENTRIES });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders shift rows with formatted duration and an in-progress shift', async () => {
    render(<TimeclockHistoryPanel />);

    expect(await screen.findByText('8h 30m')).toBeInTheDocument();
    expect(screen.getByText('In progress')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
    // 2 table rows + 1 employee-filter option
    expect(screen.getAllByText('Jamie Rivera')).toHaveLength(3);
  });

  test('filtering by employee re-queries history with the employee id', async () => {
    render(<TimeclockHistoryPanel />);
    await screen.findByText('8h 30m');

    fireEvent.change(screen.getByLabelText('Employee'), { target: { value: '1' } });

    await waitFor(() => {
      expect(timeclockHistory).toHaveBeenCalledWith(
        expect.objectContaining({ employeeId: '1' })
      );
    });
  });

  test('shows an empty state when no shifts are found', async () => {
    timeclockHistory.mockResolvedValue({ entries: [] });
    render(<TimeclockHistoryPanel />);

    expect(await screen.findByText('No shifts found for this range.')).toBeInTheDocument();
  });
});
