import { useState, useEffect } from 'react'

const KEY = 'qa_ui_state_v1'

const DEFAULTS = {
    sidebarCollapsed: false,
    filesPanelOpen: false,
}

function read() {
    try {
        const raw = localStorage.getItem(KEY)
        if (!raw) return DEFAULTS
        return { ...DEFAULTS, ...JSON.parse(raw) }
    } catch {
        return DEFAULTS
    }
}

/** Persist a small piece of UI state in localStorage. */
export function useUiState(key) {
    const [value, setValue] = useState(() => read()[key])
    useEffect(() => {
        try {
            const cur = read()
            cur[key] = value
            localStorage.setItem(KEY, JSON.stringify(cur))
        } catch { /* ignore */ }
    }, [key, value])
    return [value, setValue]
}
