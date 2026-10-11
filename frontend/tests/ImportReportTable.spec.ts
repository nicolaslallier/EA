import { fireEvent, render, screen, within } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import ImportReportTable from '../src/features/exchanges/ImportReportTable.vue'
import { aReport, anIssue } from './support/api'

function show(overrides = {}) {
  return render(ImportReportTable, { props: { report: aReport(overrides) } })
}

describe('ImportReportTable', () => {
  it('shows the counts of every category', () => {
    show({
      counts: {
        elements: { created: 3, reused: 1, rejected: 2, ignored: 4 },
        relationships: { created: 0, reused: 0, rejected: 0, ignored: 0 },
        packages: { created: 0, reused: 0, rejected: 0, ignored: 0 },
        views: { created: 0, reused: 0, rejected: 0, ignored: 0 },
      },
    })

    const row = screen.getByRole('row', { name: /Éléments/ })
    expect(within(row).getAllByRole('cell').map((cell) => cell.textContent)).toEqual(['3', '1', '2', '4'])
  })

  it('filters by severity and searches on the identifier', async () => {
    show({
      issues: [
        anIssue({ code: 'a_error', severity: 'error', source_id: 'alpha' }),
        anIssue({ code: 'b_warning', severity: 'warning', source_id: 'beta' }),
        anIssue({ code: 'c_info', severity: 'info', source_id: 'gamma' }),
      ],
    })

    await fireEvent.update(screen.getByLabelText('Gravité'), 'warning')
    expect(screen.queryByText('a_error')).toBeNull()
    expect(screen.getByText('b_warning')).toBeTruthy()

    await fireEvent.update(screen.getByLabelText('Gravité'), 'all')
    await fireEvent.update(screen.getByLabelText('Identifiant'), 'GAM')
    expect(screen.getByText('c_info')).toBeTruthy()
    expect(screen.queryByText('b_warning')).toBeNull()
  })

  it('sorts by code on demand', async () => {
    show({ issues: [anIssue({ code: 'zeta' }), anIssue({ code: 'alpha' })] })

    await fireEvent.click(screen.getByRole('button', { name: 'Trier par code' }))

    const codes = screen.getAllByTestId('issue-code').map((cell) => cell.textContent)
    expect(codes).toEqual(['alpha', 'zeta'])
  })

  it('says when the lines were cut, because the counts are still complete', () => {
    show({ truncated: true })

    expect(screen.getByText(/Rapport tronqué/)).toBeTruthy()
  })

  it('renders a hostile message as text, never as markup', () => {
    show({ issues: [anIssue({ message: '<img src=x onerror=alert(1)>' })] })

    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeTruthy()
    expect(document.querySelector('img')).toBeNull()
  })

  it('locates a row by its line or by its XML path', () => {
    show({
      issues: [
        anIssue({ code: 'csv_row', line: 7, path: null }),
        anIssue({ code: 'xml_row', line: null, path: 'elements/element[2]' }),
      ],
    })

    expect(screen.getByText('ligne 7')).toBeTruthy()
    expect(screen.getByText('elements/element[2]')).toBeTruthy()
  })
})
