// Mirrors backend/files/constants.py.
// Keep these in sync.

export const MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
export const MAX_FILE_SIZE_MB = MAX_FILE_SIZE_BYTES / 1024 / 1024

// Accept attribute for the <input type=file>.
// Includes both MIME types and extensions for broader OS coverage.
export const ACCEPT = [
    '.xlsx', '.csv', '.pdf', '.docx', '.txt', '.md',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'text/csv',
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'text/plain',
    'text/markdown',
].join(',')

export const KIND_LABELS = {
    excel: 'XLSX',
    csv: 'CSV',
    pdf: 'PDF',
    docx: 'DOCX',
    text: 'TXT',
}

// Names map to icons in components/Icon.jsx
export const KIND_ICONS = {
    excel: 'fileSpreadsheet',
    csv: 'fileSpreadsheet',
    pdf: 'fileText',
    docx: 'fileText',
    text: 'file',
}

export function kindFromMime(mime) {
    const m = (mime || '').split(';')[0].trim().toLowerCase()
    if (m === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet') return 'excel'
    if (m === 'text/csv' || m === 'application/csv') return 'csv'
    if (m === 'application/pdf') return 'pdf'
    if (m === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document') return 'docx'
    if (m === 'text/plain' || m === 'text/markdown') return 'text'
    return null
}

export function formatBytes(n) {
    if (n < 1024) return `${n} B`
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
    return `${(n / 1024 / 1024).toFixed(1)} MB`
}
