import { useEffect, useState } from 'react'
import { filesApi } from '../files/api'

/**
 * Modal that fetches and renders a preview of `file`.
 *
 * Renders:
 *   - pdf            → <iframe> via signed URL (native browser PDF viewer)
 *   - excel/csv      → simple HTML table (first 100×50)
 *   - docx/text/...  → monospace text block (extracted text)
 */
export default function FilePreviewModal({ file, onClose }) {
    const isPdf = file.mime_type === 'application/pdf'
    const [preview, setPreview] = useState(null)
    const [pdfUrl, setPdfUrl] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)

    useEffect(() => {
        let cancelled = false
        setLoading(true); setError(null); setPreview(null); setPdfUrl(null)

        const task = isPdf
            ? filesApi.download(file.id).then((r) => { if (!cancelled) setPdfUrl(r.url) })
            : filesApi.preview(file.id).then((p) => { if (!cancelled) setPreview(p) })

        task.catch((e) => { if (!cancelled) setError(e.message) })
            .finally(() => { if (!cancelled) setLoading(false) })

        return () => { cancelled = true }
    }, [file.id, isPdf])

    return (
        <div className="modal-backdrop" onClick={onClose}>
            <div className="modal" onClick={(e) => e.stopPropagation()}>
                <header className="modal-head">
                    <div>
                        <div className="modal-title">{file.filename}</div>
                        <div className="modal-sub">{file.mime_type}</div>
                    </div>
                    <button type="button" className="modal-x" onClick={onClose} aria-label="Close">✕</button>
                </header>
                <div className="modal-body">
                    {loading && <div className="modal-msg">Loading preview…</div>}
                    {error && <div className="modal-msg modal-err">{error}</div>}
                    {!loading && !error && isPdf && pdfUrl && (
                        <iframe
                            className="preview-pdf"
                            src={pdfUrl}
                            title={file.filename}
                        />
                    )}
                    {!loading && !error && !isPdf && preview?.error && (
                        <div className="modal-msg modal-err">{preview.error}</div>
                    )}
                    {!loading && !error && !isPdf && preview && !preview.error && (
                        <PreviewBody preview={preview} />
                    )}
                </div>
                {!isPdf && preview?.truncated && (
                    <footer className="modal-foot">Preview truncated — only the first portion shown.</footer>
                )}
            </div>
        </div>
    )
}

function PreviewBody({ preview }) {
    if (preview.table) return <TableView t={preview.table} />
    if (preview.text != null) return <pre className="preview-text">{preview.text}</pre>
    return <div className="modal-msg">No content.</div>
}

function TableView({ t }) {
    return (
        <div className="preview-table-wrap">
            {t.sheet_name && <div className="preview-sheet">Sheet: {t.sheet_name}</div>}
            <div className="preview-shape">{t.total_rows} rows × {t.total_cols} cols</div>
            <table className="preview-table">
                <thead>
                    <tr>
                        {t.columns.map((c, i) => <th key={i}>{c}</th>)}
                    </tr>
                </thead>
                <tbody>
                    {t.rows.map((row, i) => (
                        <tr key={i}>
                            {row.map((v, j) => <td key={j}>{formatCell(v)}</td>)}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}

function formatCell(v) {
    if (v == null) return ''
    if (typeof v === 'number') return Number.isInteger(v) ? v.toString() : v.toFixed(4)
    return String(v)
}
