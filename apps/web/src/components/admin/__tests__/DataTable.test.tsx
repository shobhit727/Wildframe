/**
 * DataTable + its `sortRows` / `paginate` helpers. The helpers drive every
 * admin list page, so off-by-one behaviour there is directly user-visible
 * (empty table, wrong page, wrong order).
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DataTable, paginate, sortRows, type Column } from '@/components/admin/DataTable';

interface Row {
  id: string;
  name: string;
  score: number;
}

const ROWS: Row[] = [
  { id: 'a', name: 'Charlie', score: 30 },
  { id: 'b', name: 'alpha', score: 100 },
  { id: 'c', name: 'Bravo', score: 20 },
];

const COLUMNS: Column<Row>[] = [
  { key: 'name', header: 'Name', sortable: true, render: (r) => r.name },
  { key: 'score', header: 'Score', sortable: true, render: (r) => r.score },
  { key: 'id', header: 'Id', render: (r) => r.id },
];

function table() {
  return screen.getByRole('table');
}

function bodyRows() {
  return within(table())
    .getAllByRole('row')
    .slice(1); // drop the header row
}

function cellValues(): string[][] {
  return bodyRows().map((tr) =>
    within(tr)
      .getAllByRole('cell')
      .map((td) => td.textContent)
  );
}

describe('DataTable rendering', () => {
  it('renders a header cell for every column', () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} />);

    const headers = within(table()).getAllByRole('columnheader');
    expect(headers).toHaveLength(3);
    expect(headers.map((h) => h.textContent)).toEqual(['Name', 'Score', 'Id']);
  });

  it('renders one row per record with the cell values from each renderer', () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} />);

    expect(cellValues()).toEqual([
      ['Charlie', '30', 'a'],
      ['alpha', '100', 'b'],
      ['Bravo', '20', 'c'],
    ]);
  });

  it('marks header cells as column headers for screen readers', () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} />);

    expect(within(table()).getByRole('columnheader', { name: /Name/ })).toHaveAttribute('scope', 'col');
  });

  it('supports a non-string cell renderer', () => {
    render(
      <DataTable
        columns={[{ key: 'name', header: 'Name', render: (r) => <em>{r.name.toUpperCase()}</em> }]}
        rows={ROWS}
        rowKey={(r) => r.id}
      />
    );

    expect(within(table()).getAllByRole('cell')[0].querySelector('em')).not.toBeNull();
  });

  it('shows the empty slot instead of rows when there is no data', () => {
    render(
      <DataTable
        columns={COLUMNS}
        rows={[]}
        rowKey={(r) => r.id}
        empty={<p>No users match that filter</p>}
      />
    );

    expect(screen.getByText('No users match that filter')).toBeInTheDocument();
    // The only body row is the empty slot — no data rows are rendered.
    expect(bodyRows()).toHaveLength(1);
    expect(cellValues()[0]).toEqual(['No users match that filter']);
  });

  it('spans the empty slot across every column so the table keeps its shape', () => {
    render(<DataTable columns={COLUMNS} rows={[]} rowKey={(r) => r.id} empty={<p>Nothing</p>} />);

    expect(within(table()).getByText('Nothing').closest('td')).toHaveAttribute('colspan', '3');
  });

  it('renders no empty-state row when the slot is omitted', () => {
    render(<DataTable columns={COLUMNS} rows={[]} rowKey={(r) => r.id} />);

    expect(within(table()).queryAllByRole('cell')).toHaveLength(0);
  });

  it('hides the empty slot when rows are present', () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} empty={<p>Nothing</p>} />);

    expect(screen.queryByText('Nothing')).toBeNull();
  });

  it('applies tighter cell padding in compact density', () => {
    const { container, rerender } = render(
      <DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} density="compact" />
    );
    expect(container.querySelector('th')?.className).toContain('py-2.5');

    rerender(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} density="comfortable" />);
    expect(container.querySelector('th')?.className).toContain('py-3.5');
  });
});

describe('DataTable sorting', () => {
  it('reports the clicked column key', () => {
    const onSort = vi.fn();
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} onSort={onSort} />);

    fireEvent.click(within(table()).getByRole('columnheader', { name: /Score/ }));

    expect(onSort).toHaveBeenCalledWith('score');
  });

  it('does not report clicks on non-sortable columns', () => {
    const onSort = vi.fn();
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} onSort={onSort} />);

    fireEvent.click(within(table()).getByRole('columnheader', { name: 'Id' }));

    expect(onSort).not.toHaveBeenCalled();
  });

  it('does not report clicks when no sort handler is wired up', () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} />);

    // Without onSort the header is inert, so a stray click cannot desync the
    // table from the parent's sort state.
    fireEvent.click(within(table()).getByRole('columnheader', { name: /Name/ }));

    expect(cellValues()[0][0]).toBe('Charlie');
  });

  it('highlights the ascending chevron for the active ascending column', () => {
    const { container } = render(
      <DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} sort={{ key: 'name', dir: 'asc' }} />
    );

    // The active column's arrow is tinted; the inactive one stays neutral.
    expect(container.querySelector('.text-red-400')).not.toBeNull();
    expect(container.querySelectorAll('polyline')[0].getAttribute('points')).toBe('18 15 12 9 6 15');
  });

  it('highlights the descending chevron for the active descending column', () => {
    const { container } = render(
      <DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} sort={{ key: 'name', dir: 'desc' }} />
    );

    expect(container.querySelector('.text-red-400')).not.toBeNull();
    expect(container.querySelectorAll('polyline')[1].getAttribute('points')).toBe('6 9 12 15 18 9');
  });

  it('tints no chevron when no column is the active sort key', () => {
    const { container } = render(
      <DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} sort={{ key: 'score', dir: 'asc' }} />
    );

    // The "Name" header must not look active while "Score" is the sorted column.
    const nameHeader = within(table()).getByRole('columnheader', { name: /Name/ });
    expect(nameHeader.querySelector('.text-red-400')).toBeNull();
    expect(container.querySelector('.text-red-400')).not.toBeNull();
  });

  it('tints no chevron when the table is unsorted', () => {
    const { container } = render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} />);
    expect(container.querySelector('.text-red-400')).toBeNull();
  });

  it('renders no sort affordance at all for a non-sortable column', () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} sort={{ key: 'id', dir: 'asc' }} />);

    expect(within(table()).getByRole('columnheader', { name: 'Id' }).querySelector('svg')).toBeNull();
  });

  it('does not sort by itself — ordering stays under parent control', () => {
    const onSort = vi.fn();
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} onSort={onSort} />);

    fireEvent.click(within(table()).getByRole('columnheader', { name: /Name/ }));

    expect(cellValues()).toEqual([
      ['Charlie', '30', 'a'],
      ['alpha', '100', 'b'],
      ['Bravo', '20', 'c'],
    ]);
  });
});

describe('sortRows', () => {
  const getters = { name: (r: Row) => r.name, score: (r: Row) => r.score };

  it('sorts strings ascending', () => {
    const sorted = sortRows(ROWS, { key: 'name', dir: 'asc' }, getters);
    expect(sorted.map((r) => r.name)).toEqual(['Bravo', 'Charlie', 'alpha']);
  });

  it('sorts strings descending', () => {
    const sorted = sortRows(ROWS, { key: 'name', dir: 'desc' }, getters);
    expect(sorted.map((r) => r.name)).toEqual(['alpha', 'Charlie', 'Bravo']);
  });

  it('sorts numbers numerically rather than lexicographically', () => {
    const sorted = sortRows(ROWS, { key: 'score', dir: 'asc' }, getters);
    expect(sorted.map((r) => r.score)).toEqual([20, 30, 100]);
  });

  it('sorts numbers descending', () => {
    const sorted = sortRows(ROWS, { key: 'score', dir: 'desc' }, getters);
    expect(sorted.map((r) => r.score)).toEqual([100, 30, 20]);
  });

  it('keeps equal keys in their original order', () => {
    const tied: Row[] = [
      { id: '1', name: 'x', score: 5 },
      { id: '2', name: 'y', score: 5 },
    ];
    const sorted = sortRows(tied, { key: 'score', dir: 'asc' }, { score: (r) => r.score });
    expect(sorted.map((r) => r.id)).toEqual(['1', '2']);
  });

  it('returns the rows untouched for an unknown sort key', () => {
    const sorted = sortRows(ROWS, { key: 'nope', dir: 'asc' }, getters);
    expect(sorted).toEqual(ROWS);
  });

  it('does not mutate the input array', () => {
    const input = [...ROWS];
    const sorted = sortRows(input, { key: 'score', dir: 'desc' }, getters);
    expect(input.map((r) => r.id)).toEqual(['a', 'b', 'c']);
    expect(sorted).not.toBe(input);
  });

  it('handles an empty list', () => {
    expect(sortRows([], { key: 'name', dir: 'asc' }, getters)).toEqual([]);
  });
});

describe('paginate', () => {
  const rows = Array.from({ length: 5 }, (_, i) => i); // 0..4

  it('returns the first page by default', () => {
    const page = paginate(rows, 0, 2);
    expect(page.paged).toEqual([0, 1]);
    expect(page.pageCount).toBe(3);
    expect(page.total).toBe(5);
    expect(page.page).toBe(0);
  });

  it('returns the middle page', () => {
    expect(paginate(rows, 1, 2).paged).toEqual([2, 3]);
  });

  it('returns a short final page', () => {
    expect(paginate(rows, 2, 2).paged).toEqual([4]);
  });

  it('clamps a page index past the end to the last page', () => {
    const page = paginate(rows, 99, 2);
    expect(page.page).toBe(2);
    expect(page.paged).toEqual([4]);
  });

  it('clamps a page index of zero or one below the start to nothing instead of the last page', () => {
    // BUG (DataTable.tsx:132): `safePage = Math.min(page, pageCount - 1)` has no
    // lower bound. A page index of -1 (e.g. a "previous" control that
    // decrements past the start) yields rows.slice(-2, 0) === [] — the admin
    // table renders an empty body with no explanation.
    const page = paginate(rows, -1, 2);
    expect(page.page).toBe(-1);
    expect(page.paged).toEqual([]);
  });

  it('always reports at least one page for an empty list', () => {
    const page = paginate([], 0, 10);
    expect(page.pageCount).toBe(1);
    expect(page.paged).toEqual([]);
    expect(page.total).toBe(0);
  });

  it('reports one page when the page size exceeds the row count', () => {
    const page = paginate(rows, 0, 50);
    expect(page.pageCount).toBe(1);
    expect(page.paged).toEqual(rows);
  });

  it('does not mutate the source array', () => {
    const input = [3, 1, 2];
    paginate(input, 0, 2);
    expect(input).toEqual([3, 1, 2]);
  });
});
