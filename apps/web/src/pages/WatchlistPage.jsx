import { useState, useEffect } from 'react'
import { api } from '../api'

const TIMEZONES = [
  'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
  'Europe/London', 'Europe/Berlin', 'Asia/Shanghai', 'Asia/Tokyo', 'Asia/Kolkata',
  'Australia/Sydney', 'Pacific/Auckland',
]

export default function WatchlistPage() {
  const [tickers, setTickers] = useState([])
  const [watchlist, setWatchlist] = useState([])
  const [prefs, setPrefs] = useState({ timezone: 'America/New_York', briefing_enabled: true })
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([api.getTickers(), api.getWatchlist(), api.getPreferences()])
      .then(([t, w, p]) => {
        setTickers(t)
        setWatchlist(w)
        if (p.timezone) setPrefs(p)
      })
      .finally(() => setLoading(false))
  }, [])

  const watchedSet = new Set(watchlist.map(w => w.ticker))

  const toggleTicker = async (ticker) => {
    if (watchedSet.has(ticker)) {
      await api.removeFromWatchlist(ticker)
      setWatchlist(prev => prev.filter(w => w.ticker !== ticker))
    } else {
      await api.addToWatchlist(ticker)
      setWatchlist(prev => [...prev, { ticker, added_at: new Date().toISOString() }])
    }
  }

  const savePrefs = async () => {
    await api.updatePreferences(prefs)
    alert('Preferences saved!')
  }

  if (loading) return <div style={{ padding: 40, color: '#71717a' }}>Loading...</div>

  return (
    <div style={{ maxWidth: 640 }}>
      <h1 style={{ fontSize: '1.5rem', fontWeight: 700, marginBottom: 8 }}>Watchlist</h1>
      <p style={{ color: '#a1a1aa', marginBottom: 24, fontSize: '0.875rem' }}>
        Select tickers to track. Daily briefings will be generated for your watchlist.
      </p>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(100px, 1fr))',
        gap: 8,
        marginBottom: 32,
      }}>
        {tickers.map(t => {
          const active = watchedSet.has(t.ticker)
          return (
            <button
              key={t.ticker}
              onClick={() => toggleTicker(t.ticker)}
              style={{
                padding: '10px 8px',
                background: active ? '#1d4ed8' : '#27272a',
                color: active ? '#fff' : '#a1a1aa',
                borderRadius: 8,
                fontSize: '0.8rem',
                fontWeight: active ? 600 : 400,
                border: active ? '1px solid #3b82f6' : '1px solid #3f3f46',
              }}
            >
              {t.ticker}
              <div style={{ fontSize: '0.65rem', opacity: 0.7, marginTop: 2 }}>{t.ticker_type}</div>
            </button>
          )
        })}
      </div>

      <h2 style={{ fontSize: '1.125rem', fontWeight: 600, marginBottom: 12 }}>Preferences</h2>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 16 }}>
        <label style={{ fontSize: '0.875rem', color: '#a1a1aa' }}>
          Timezone
          <select
            value={prefs.timezone}
            onChange={e => setPrefs(p => ({ ...p, timezone: e.target.value }))}
            style={{ display: 'block', marginTop: 4, width: '100%' }}
          >
            {TIMEZONES.map(tz => <option key={tz} value={tz}>{tz}</option>)}
          </select>
        </label>

        <label style={{ fontSize: '0.875rem', color: '#a1a1aa', display: 'flex', alignItems: 'center', gap: 8 }}>
          <input
            type="checkbox"
            checked={prefs.briefing_enabled}
            onChange={e => setPrefs(p => ({ ...p, briefing_enabled: e.target.checked }))}
          />
          Enable daily briefing at 8:00 AM
        </label>
      </div>

      <button
        onClick={savePrefs}
        style={{ background: '#3b82f6', color: '#fff', padding: '10px 24px' }}
      >
        Save Preferences
      </button>
    </div>
  )
}
