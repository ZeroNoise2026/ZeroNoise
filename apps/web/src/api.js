// In dev: Vite proxies '/api' → http://localhost:8000 (see vite.config.js).
// In prod (Vercel): set VITE_API_URL=https://<cloud-run-backend>.run.app
// and API calls go directly to the Cloud Run backend.
import { supabase } from './auth/supabaseClient'

const API_BASE = (import.meta.env.VITE_API_URL
  ? `${import.meta.env.VITE_API_URL.replace(/\/$/, '')}/api`
  : '/api')

async function getAccessToken() {
  const { data } = await supabase.auth.getSession()
  return data.session?.access_token ?? null
}

async function request(path, options = {}) {
  const token = await getAccessToken()
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...options.headers,
  }
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.message || 'Request failed')
  }
  // 204 No Content (e.g. DELETE endpoints) — no body to parse
  if (res.status === 204) return null
  return res.json()
}

export const api = {
  getTickers: () => request('/tickers'),
  getWatchlist: () => request('/watchlist'),
  addToWatchlist: (ticker) => request('/watchlist', { method: 'POST', body: JSON.stringify({ ticker }) }),
  removeFromWatchlist: (ticker) => request(`/watchlist/${ticker}`, { method: 'DELETE' }),

  getPreferences: () => request('/preferences'),
  updatePreferences: (prefs) => request('/preferences', { method: 'PUT', body: JSON.stringify(prefs) }),

  getBriefings: (limit = 7) => request(`/briefings?limit=${limit}`),
  getLatestBriefing: () => request('/briefings/latest'),
  getBriefingByDate: (date) => request(`/briefings/by-date?date=${date}`),
  getBriefingDates: (dateFrom, dateTo) =>
    request(`/briefings/dates?date_from=${dateFrom}&date_to=${dateTo}`),
  refreshBriefing: () => request('/briefings/refresh', { method: 'POST' }),

  /**
   * SSE streaming chat.
   *   - `question`: user query
   *   - `opts.forceTicker`: string — explicit force-bind (e.g. from clarification chip)
   *   - `opts.contextTickers`: string[] — user's watchlist, fallback scope only
   * Callbacks: onToken, onThinking, onStatus, onError, onDone,
   *            onClarification({question, options}), onSession, onSources(items)
   * Returns an abort controller so the caller can cancel.
   */
  chatStream: async (question, opts = {}) => {
    const {
      forceTicker = null,
      contextTickers = [],
      sessionId = null,
      onToken, onThinking, onStatus, onError, onDone, onClarification, onSession, onSources,
    } = opts
    const controller = new AbortController()
    try {
      const token = await getAccessToken()
      const res = await fetch(`${API_BASE}/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          question,
          ticker: forceTicker || null,
          context_tickers: contextTickers,
          session_id: sessionId,
        }),
        signal: controller.signal,
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(err.detail || 'Stream request failed')
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() // keep incomplete line in buffer

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6)
            if (data === '[DONE]') {
              onDone?.()
              return controller
            }
            if (data.startsWith('[Error:')) {
              onError?.(data.replace(/^\[Error:\s*/, '').replace(/\]$/, ''))
              onDone?.()
              return controller
            }
            try {
              const parsed = JSON.parse(data)
              if (parsed.type === 'status') onStatus?.(parsed.text)
              else if (parsed.type === 'thinking') onThinking?.(parsed.text)
              else if (parsed.type === 'token') onToken?.(parsed.text)
              else if (parsed.type === 'clarification') {
                try {
                  const inner = JSON.parse(parsed.text)
                  onClarification?.(inner)
                } catch { onClarification?.({ question, options: [] }) }
              }
              else if (parsed.type === 'session') onSession?.(parsed)
              else if (parsed.type === 'sources') onSources?.(parsed.items || [])
              else onToken?.(parsed.text || data)
            } catch { onToken?.(data) }
          }
        }
      }
      onDone?.()
    } catch (err) {
      if (err.name !== 'AbortError') {
        onError?.(err.message)
      }
      onDone?.()
    }
    return controller
  },

  summarize: (ticker) => request('/summarize', {
    method: 'POST',
    body: JSON.stringify({ ticker }),
  }),

  // ── Chat sessions ────────────────────────────────────────
  listChatSessions: () => request('/chat/sessions'),
  getChatSessionMessages: (id) => request(`/chat/sessions/${id}/messages`),
  deleteChatSession: (id) => request(`/chat/sessions/${id}`, { method: 'DELETE' }),
}
