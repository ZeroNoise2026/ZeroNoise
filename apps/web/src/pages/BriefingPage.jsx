import { useState, useEffect, useMemo, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import { api } from '../api'
import { fmtDate as fmt, today, daysAgo } from '../utils/date'
import { colors } from '../utils/theme'

export default function BriefingPage() {
  const [selectedDate, setSelectedDate] = useState(fmt(today()))
  const [briefing, setBriefing] = useState(null)
  const [availableDates, setAvailableDates] = useState(new Set())  // 'YYYY-MM-DD' strings
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState(null)
  const hydratedRef = useRef(false)  // don't clobber the user's pick with the default

  const maxDate = useMemo(() => fmt(today()), [])
  const minDate = useMemo(() => fmt(daysAgo(365)), [])

  // Load set of dates that have briefings (last year)
  useEffect(() => {
    api.getBriefingDates(minDate, maxDate)
      .then(({ dates }) => {
        const list = dates || []
        setAvailableDates(new Set(list))
        if (!hydratedRef.current && list.length > 0) {
          setSelectedDate(list[0])
        }
        hydratedRef.current = true
      })
      .catch(() => {
        setAvailableDates(new Set())
        hydratedRef.current = true
      })
  }, [minDate, maxDate])

  // Load briefing whenever selectedDate changes
  useEffect(() => {
    if (!selectedDate) return
    setLoading(true)
    setError(null)
    api.getBriefingByDate(selectedDate)
      .then(setBriefing)
      .catch((e) => {
        setBriefing(null)
        if (!String(e.message).toLowerCase().includes('no briefing')) {
          setError(e.message)
        }
      })
      .finally(() => setLoading(false))
  }, [selectedDate])

  const handleRefresh = async () => {
    setRefreshing(true)
    setError(null)
    try {
      const { briefing_date } = await api.refreshBriefing()
      const { dates } = await api.getBriefingDates(minDate, maxDate)
      setAvailableDates(new Set(dates || []))
      setSelectedDate(briefing_date)
    } catch (e) {
      setError(e.message)
    } finally {
      setRefreshing(false)
    }
  }

  const handlePick = (value) => {
    hydratedRef.current = true
    setSelectedDate(value)
  }

  return (
    <div style={{ maxWidth: 720 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 700 }}>Daily Briefing</h1>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          style={{
            padding: '8px 16px',
            fontSize: '0.85rem',
            fontWeight: 600,
            background: refreshing ? colors.bgElevated : colors.primary,
            color: '#fff',
            border: 'none',
            borderRadius: 8,
            cursor: refreshing ? 'not-allowed' : 'pointer',
            opacity: refreshing ? 0.6 : 1,
          }}
        >
          {refreshing ? 'Generating...' : '↻ Refresh'}
        </button>
      </div>

      <div style={{ marginBottom: 16 }}>
        <input
          type="date"
          value={selectedDate}
          min={minDate}
          max={maxDate}
          onChange={(e) => handlePick(e.target.value)}
          style={{
            padding: '6px 10px',
            background: colors.bg,
            color: colors.text,
            border: `1px solid ${colors.border}`,
            borderRadius: 6,
            fontSize: '0.85rem',
            colorScheme: 'dark',
          }}
        />
      </div>

      {error && <p style={{ color: colors.error, fontSize: '0.85rem', marginBottom: 12 }}>{error}</p>}

      {loading ? (
        <div style={{ padding: 40, color: colors.textDim }}>Loading...</div>
      ) : briefing && briefing.content ? (
        <div style={{
          background: colors.bg,
          borderRadius: 12,
          border: `1px solid ${colors.borderSoft}`,
          padding: 24,
        }}>
          <div style={{ fontSize: '0.75rem', color: colors.textDim, marginBottom: 12 }}>
            {briefing.briefing_date} &middot; {briefing.tickers?.join(', ')}
          </div>
          <div className="markdown-body" style={{ lineHeight: 1.7, fontSize: '0.9rem' }}>
            <ReactMarkdown>{briefing.content}</ReactMarkdown>
          </div>
        </div>
      ) : (
        <div style={{
          padding: 32,
          background: colors.bg,
          borderRadius: 12,
          border: `1px solid ${colors.borderSoft}`,
          textAlign: 'center',
          color: colors.textDim,
        }}>
          <p style={{ fontSize: '1.05rem', marginBottom: 8 }}>No briefing for {selectedDate}</p>
          <p style={{ fontSize: '0.85rem' }}>
            {availableDates.size === 0
              ? 'Add tickers to your watchlist and click Refresh to generate one.'
              : 'Pick a different date, or click Refresh to generate today\'s briefing.'}
          </p>
        </div>
      )}
    </div>
  )
}
