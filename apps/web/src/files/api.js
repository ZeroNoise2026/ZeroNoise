// Files API helpers. Keeps file-upload logic out of the generic api.js so the
// hot path (chat/streaming) stays small.

import { supabase } from '../auth/supabaseClient'

const API_BASE = (import.meta.env.VITE_API_URL
    ? `${import.meta.env.VITE_API_URL.replace(/\/$/, '')}/api/files`
    : '/api/files')

async function authHeaders() {
    const { data } = await supabase.auth.getSession()
    const token = data.session?.access_token
    return token ? { Authorization: `Bearer ${token}` } : {}
}

async function jsonFetch(path, options = {}) {
    const res = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: { ...(await authHeaders()), ...(options.headers || {}) },
    })
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(err.detail || 'Request failed')
    }
    if (res.status === 204) return null
    return res.json()
}

export const filesApi = {
    /**
     * Upload a single File object.
     * onProgress: (0..1) — coarse progress; XHR is used for upload progress
     * since fetch doesn't expose request progress in the browser.
     */
    upload(file, { sessionId = null, onProgress } = {}) {
        return new Promise(async (resolve, reject) => {
            try {
                const headers = await authHeaders()
                const fd = new FormData()
                fd.append('file', file)
                if (sessionId) fd.append('session_id', sessionId)

                const xhr = new XMLHttpRequest()
                xhr.open('POST', API_BASE)
                Object.entries(headers).forEach(([k, v]) => xhr.setRequestHeader(k, v))

                xhr.upload.onprogress = (e) => {
                    if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total)
                }
                xhr.onload = () => {
                    if (xhr.status >= 200 && xhr.status < 300) {
                        try { resolve(JSON.parse(xhr.responseText)) }
                        catch { resolve(null) }
                    } else {
                        let detail = xhr.statusText
                        try { detail = JSON.parse(xhr.responseText).detail || detail } catch { }
                        reject(new Error(detail))
                    }
                }
                xhr.onerror = () => reject(new Error('Network error'))
                xhr.send(fd)
            } catch (e) { reject(e) }
        })
    },

    list(sessionId, createdBy) {
        const params = new URLSearchParams()
        if (sessionId) params.set('session_id', sessionId)
        if (createdBy) params.set('created_by', createdBy)
        const q = params.toString() ? `?${params}` : ''
        return jsonFetch(q)
    },

    get(id) { return jsonFetch(`/${id}`) },

    preview(id) { return jsonFetch(`/${id}/preview`) },

    /** Returns { url, filename, mime_type } — short-lived signed URL. */
    download(id) { return jsonFetch(`/${id}/download`) },

    attach(id, sessionId) {
        return jsonFetch(`/${id}/attach`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId }),
        })
    },

    remove(id) { return jsonFetch(`/${id}`, { method: 'DELETE' }) },
}
