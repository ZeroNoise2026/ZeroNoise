import { useCallback, useRef, useState } from 'react'
import { filesApi } from '../files/api'
import {
    ACCEPT,
    MAX_FILE_SIZE_MB,
    MAX_FILE_SIZE_BYTES,
} from '../files/constants'
import FileCard from './FileCard'
import { Icon } from './Icon'

/**
 * Single-row chat composer with inline attachments + drag-to-upload.
 *
 * Layout:
 *   ┌─────────────────────────────────────────┐
 *   │ [pill] [pill] [pill]                    │  (only if attachments)
 *   │ [📎]  type your message…           [↑]  │  (input row)
 *   └─────────────────────────────────────────┘
 *   The whole box is a drop target. Visual ring on drag-over.
 *
 * Props:
 *   value, onChange(string)
 *   onSubmit()                           — fires on Enter or Send button
 *   disabled
 *   sessionId?
 *   attachedFiles: FileRecord[]
 *   onAttach(record)                     — newly uploaded record
 *   onDetach(record)                     — remove pill
 *   onPreview(record)
 *   onError?(msg)
 */
export default function ChatComposer({
    value,
    onChange,
    onSubmit,
    disabled,
    sessionId,
    attachedFiles,
    onAttach,
    onDetach,
    onPreview,
    onError,
}) {
    const inputRef = useRef(null)
    const [dragActive, setDragActive] = useState(false)
    const [uploads, setUploads] = useState([])

    const handleFiles = useCallback(async (fileList) => {
        const files = Array.from(fileList || [])
        for (const f of files) {
            if (f.size > MAX_FILE_SIZE_BYTES) {
                onError?.(`${f.name} exceeds ${MAX_FILE_SIZE_MB} MB`)
                continue
            }
            const localId = `${f.name}-${Date.now()}-${Math.random()}`
            setUploads((u) => [...u, { id: localId, name: f.name, progress: 0 }])
            try {
                const rec = await filesApi.upload(f, {
                    sessionId,
                    onProgress: (p) => setUploads((u) =>
                        u.map((x) => x.id === localId ? { ...x, progress: p } : x)
                    ),
                })
                setUploads((u) => u.filter((x) => x.id !== localId))
                onAttach?.(rec)
            } catch (err) {
                setUploads((u) => u.map((x) =>
                    x.id === localId ? { ...x, error: err.message } : x
                ))
                onError?.(err.message)
            }
        }
    }, [sessionId, onAttach, onError])

    const onDrop = (e) => {
        e.preventDefault()
        setDragActive(false)
        handleFiles(e.dataTransfer.files)
    }

    const onKeyDown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            if (!disabled && value.trim()) onSubmit?.()
        }
    }

    return (
        <div
            className={`composer${dragActive ? ' composer--drag' : ''}`}
            onDragOver={(e) => { e.preventDefault(); setDragActive(true) }}
            onDragLeave={() => setDragActive(false)}
            onDrop={onDrop}
        >
            {(attachedFiles.length > 0 || uploads.length > 0) && (
                <div className="composer-attachments">
                    {attachedFiles.map((f) => (
                        <FileCard
                            key={f.id}
                            file={f}
                            onPreview={onPreview}
                            onRemove={onDetach}
                        />
                    ))}
                    {uploads.map((u) => (
                        <div key={u.id} className="composer-upload">
                            <span className="composer-upload-name">{u.name}</span>
                            {u.error
                                ? <span className="composer-upload-err">{u.error}</span>
                                : <span className="composer-upload-pct">{Math.round(u.progress * 100)}%</span>}
                        </div>
                    ))}
                </div>
            )}

            <div className="composer-row">
                <input
                    ref={inputRef}
                    type="file"
                    multiple
                    accept={ACCEPT}
                    onChange={(e) => { handleFiles(e.target.files); e.target.value = '' }}
                    style={{ display: 'none' }}
                />
                <button
                    type="button"
                    className="composer-attach"
                    title="Attach files"
                    disabled={disabled}
                    onClick={() => inputRef.current?.click()}
                ><Icon name="paperclip" size={18} /></button>

                <textarea
                    className="composer-input"
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    onKeyDown={onKeyDown}
                    placeholder="Ask about a stock or the market…"
                    rows={1}
                    disabled={disabled}
                />

                <button
                    type="button"
                    className="composer-send"
                    disabled={disabled || !value.trim()}
                    onClick={() => onSubmit?.()}
                    aria-label="Send"
                ><Icon name="arrowUp" size={18} /></button>
            </div>
        </div>
    )
}
