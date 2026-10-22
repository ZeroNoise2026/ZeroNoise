// Shared date helpers for the UI.

export const fmtDate = (d) => d.toISOString().slice(0, 10)
export const today = () => new Date()
export const daysAgo = (n) => {
    const d = new Date()
    d.setDate(d.getDate() - n)
    return d
}

export function relativeTime(iso) {
    if (!iso) return ''
    const diff = (Date.now() - new Date(iso).getTime()) / 1000
    if (diff < 60) return 'just now'
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
    if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`
    return new Date(iso).toLocaleDateString()
}
