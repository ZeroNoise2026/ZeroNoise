import { KIND_ICONS, KIND_LABELS, kindFromMime, formatBytes } from '../files/constants'
import { Icon } from './Icon'

/**
 * Compact pill that shows a file's name, size, and lets the user preview/remove.
 *
 * Props:
 *   file: FileRecord                     — backend row
 *   onPreview?(file): void
 *   onRemove?(file): void                — if provided, shows ✕ button
 */
export default function FileCard({ file, onPreview, onRemove }) {
    const kind = kindFromMime(file.mime_type) || 'text'
    const meta = file.parsed_meta || {}
    const subtitle = subtitleFor(kind, meta, file.size_bytes)

    return (
        <div className="fcard">
            <div className="fcard-icon" aria-hidden>
                <Icon name={KIND_ICONS[kind] || 'file'} size={18} />
            </div>
            <button
                type="button"
                className="fcard-body"
                onClick={() => onPreview?.(file)}
                title="Preview"
            >
                <div className="fcard-name">{file.filename}</div>
                <div className="fcard-sub">{KIND_LABELS[kind]} · {subtitle}</div>
            </button>
            {onRemove && (
                <button
                    type="button"
                    className="fcard-x"
                    onClick={(e) => { e.stopPropagation(); onRemove(file) }}
                    aria-label="Remove file"
                >
                    <Icon name="close" size={14} />
                </button>
            )}
        </div>
    )
}

function subtitleFor(kind, meta, size) {
    if (meta.error) return `error · ${formatBytes(size)}`
    if (kind === 'excel' && meta.sheets?.length) {
        const s = meta.sheets[0]
        const more = meta.sheets.length > 1 ? ` (+${meta.sheets.length - 1})` : ''
        return `${s.rows}×${s.cols}${more} · ${formatBytes(size)}`
    }
    if (kind === 'csv') return `${meta.rows ?? '?'} rows · ${formatBytes(size)}`
    if (kind === 'pdf') return `${meta.page_count ?? '?'} pages · ${formatBytes(size)}`
    if (kind === 'docx') return `${meta.paragraphs ?? '?'} paragraphs · ${formatBytes(size)}`
    if (kind === 'text') return `${meta.lines ?? '?'} lines · ${formatBytes(size)}`
    return formatBytes(size)
}
