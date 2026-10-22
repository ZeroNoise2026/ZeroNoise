import { useEffect, useState, useCallback } from 'react'
import { useUiState } from '../utils/uiState'
import { filesApi } from '../files/api'
import { KIND_ICONS, KIND_LABELS, kindFromMime, formatBytes } from '../files/constants'
import { Icon } from './Icon'

/**
 * Right-side document panel. Bank-style, neutral palette.
 * Lists every file owned by the current user, segmented into two tabs:
 *   - "Uploaded" (created_by = 'user')
 *   - "Generated" (created_by = 'assistant')  ← empty until Phase 3.
 *
 * Collapses to a thin tab; state persists in localStorage.
 */
export default function FilesPanel({ refreshKey, onPreview, onRemove }) {
    const [open, setOpen] = useUiState('filesPanelOpen')
    const [tab, setTab] = useState('user')   // 'user' | 'assistant'
    const [files, setFiles] = useState([])
    const [loading, setLoading] = useState(false)
    const [err, setErr] = useState(null)

    const refresh = useCallback(async () => {
        setLoading(true); setErr(null)
        try {
            const list = await filesApi.list()
            setFiles(list || [])
        } catch (e) {
            setErr(e.message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => { if (open) refresh() }, [open, refresh, refreshKey])

    const userFiles = files.filter((f) => (f.created_by || 'user') === 'user')
    const aiFiles = files.filter((f) => f.created_by === 'assistant')
    const shown = tab === 'user' ? userFiles : aiFiles

    const handleRemove = async (f) => {
        try {
            await filesApi.remove(f.id)
            setFiles((list) => list.filter((x) => x.id !== f.id))
            onRemove?.(f)
        } catch (e) { setErr(e.message) }
    }

    if (!open) {
        return (
            <button
                type="button"
                className="docpanel-tab"
                onClick={() => setOpen(true)}
                title="Show documents"
                aria-label="Show documents"
            >
                <Icon name="folder" size={16} />
                <span className="docpanel-tab-label">Documents</span>
            </button>
        )
    }

    return (
        <aside className="docpanel">
            <header className="docpanel-head">
                <div className="docpanel-title">Documents</div>
                <button
                    type="button"
                    className="docpanel-collapse"
                    onClick={() => setOpen(false)}
                    title="Hide"
                    aria-label="Hide documents panel"
                ><Icon name="chevronRight" size={14} /></button>
            </header>

            <div className="docpanel-tabs" role="tablist">
                <button
                    role="tab"
                    aria-selected={tab === 'user'}
                    className={`docpanel-tab-btn${tab === 'user' ? ' active' : ''}`}
                    onClick={() => setTab('user')}
                >
                    Uploaded
                    <span className="docpanel-count">{userFiles.length}</span>
                </button>
                <button
                    role="tab"
                    aria-selected={tab === 'assistant'}
                    className={`docpanel-tab-btn${tab === 'assistant' ? ' active' : ''}`}
                    onClick={() => setTab('assistant')}
                >
                    Generated
                    <span className="docpanel-count">{aiFiles.length}</span>
                </button>
            </div>

            <div className="docpanel-body">
                {loading && <div className="docpanel-empty">Loading…</div>}
                {err && <div className="docpanel-empty docpanel-err">{err}</div>}
                {!loading && !err && shown.length === 0 && (
                    <div className="docpanel-empty">
                        {tab === 'user'
                            ? 'No documents uploaded yet.'
                            : 'No documents generated yet.'}
                    </div>
                )}
                {!loading && !err && shown.length > 0 && (
                    <ul className="doclist">
                        {shown.map((f) => (
                            <DocRow
                                key={f.id}
                                file={f}
                                onPreview={onPreview}
                                onRemove={tab === 'user' ? handleRemove : undefined}
                            />
                        ))}
                    </ul>
                )}
            </div>
        </aside>
    )
}

function DocRow({ file, onPreview, onRemove }) {
    const kind = kindFromMime(file.mime_type) || 'text'
    const meta = file.parsed_meta || {}
    return (
        <li className="docrow">
            <button
                type="button"
                className="docrow-main"
                onClick={() => onPreview?.(file)}
                title="Preview"
            >
                <Icon name={KIND_ICONS[kind] || 'file'} size={18} className="docrow-ic" />
                <div className="docrow-text">
                    <div className="docrow-name">{file.filename}</div>
                    <div className="docrow-meta">
                        <span className="docrow-kind">{KIND_LABELS[kind]}</span>
                        <span className="docrow-dot">·</span>
                        <span>{formatBytes(file.size_bytes)}</span>
                        {meta.page_count && <>
                            <span className="docrow-dot">·</span>
                            <span>{meta.page_count} pages</span>
                        </>}
                        {meta.rows && <>
                            <span className="docrow-dot">·</span>
                            <span>{meta.rows} rows</span>
                        </>}
                    </div>
                </div>
            </button>
            {onRemove && (
                <button
                    type="button"
                    className="docrow-del"
                    onClick={() => onRemove(file)}
                    title="Remove"
                    aria-label="Remove document"
                ><Icon name="trash" size={14} /></button>
            )}
        </li>
    )
}
