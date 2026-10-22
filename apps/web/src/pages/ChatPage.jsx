import { useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import { relativeTime } from '../utils/date'
import { useUiState } from '../utils/uiState'
import ChatComposer from '../components/ChatComposer'
import FilePreviewModal from '../components/FilePreviewModal'
import FilesPanel from '../components/FilesPanel'
import { Icon } from '../components/Icon'

/**
 * Citation block shown under each assistant reply.
 *
 * Accepts a uniform Source schema across all 3 retrieval paths:
 *   {id, doc_type, ticker, date, title, url, label, similarity}
 *
 * Render rules:
 *   - url present                  -> clickable anchor
 *   - title present, no url        -> grey "<title> (no link)"
 *   - else                         -> grey label
 */
function SourcesBlock({ items }) {
  const [open, setOpen] = useState(false)
  if (!Array.isArray(items) || items.length === 0) return null

  return (
    <div style={{ marginTop: 10, fontSize: '0.75rem' }}>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          cursor: 'pointer',
          color: '#a1a1aa',
          userSelect: 'none',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 4,
        }}
      >
        <span style={{ fontSize: '0.7rem' }}>{open ? '▾' : '▸'}</span>
        📎 Sources ({items.length})
      </div>
      {open && (
        <ol style={{ margin: '6px 0 0 0', paddingLeft: 22, color: '#a1a1aa', lineHeight: 1.5 }}>
          {items.map((s, i) => (
            <li key={s.id || i} style={{ marginBottom: 2 }}>
              {s.url ? (
                <a
                  href={s.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: '#60a5fa', textDecoration: 'none' }}
                >
                  {s.title || s.label || s.id}
                </a>
              ) : s.title ? (
                <span style={{ color: '#71717a' }}>
                  {s.title} <span style={{ color: '#52525b' }}>(no link)</span>
                </span>
              ) : (
                <span style={{ color: '#71717a' }}>{s.label || s.id}</span>
              )}
              {(s.ticker || s.date) && (
                <span style={{ color: '#52525b', marginLeft: 6, fontSize: '0.7rem' }}>
                  {s.ticker ? `· ${s.ticker}` : ''}{s.date ? ` · ${s.date}` : ''}
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

export default function ChatPage() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [watchlist, setWatchlist] = useState([])
  const [elapsed, setElapsed] = useState(0)
  const [sessions, setSessions] = useState([])
  const [currentSessionId, setCurrentSessionId] = useState(null)
  const [loadingMessages, setLoadingMessages] = useState(false)
  const [attachedFiles, setAttachedFiles] = useState([])
  const [previewFile, setPreviewFile] = useState(null)
  const [filesRefreshKey, setFilesRefreshKey] = useState(0)
  const [sidebarCollapsed] = useUiState('sidebarCollapsed')
  const [sidebarSlot, setSidebarSlot] = useState(null)
  const bottomRef = useRef(null)
  const timerRef = useRef(null)

  // Locate the sidebar slot. Re-runs whenever sidebar collapse state changes
  // (slot only exists when sidebar is expanded).
  useEffect(() => {
    setSidebarSlot(document.getElementById('sidebar-extra-slot'))
  }, [sidebarCollapsed])

  useEffect(() => {
    api.getWatchlist()
      .then(items => setWatchlist((items || []).map(w => w.ticker)))
      .catch(() => setWatchlist([]))
    refreshSessions()
  }, [])

  const refreshSessions = useCallback(async () => {
    try {
      const list = await api.listChatSessions()
      setSessions(list || [])
    } catch {
      setSessions([])
    }
  }, [])

  useEffect(() => {
    if (loading) {
      setElapsed(0)
      timerRef.elapsed = 0
      timerRef.current = setInterval(() => {
        setElapsed(t => { timerRef.elapsed = t + 1; return t + 1 })
      }, 1000)
    } else {
      clearInterval(timerRef.current)
    }
    return () => clearInterval(timerRef.current)
  }, [loading])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const loadSession = async (sid) => {
    if (loading) return
    setCurrentSessionId(sid)
    setLoadingMessages(true)
    try {
      const resp = await api.getChatSessionMessages(sid)
      const msgs = Array.isArray(resp) ? resp : (resp?.messages || [])
      setMessages(msgs.map(m => ({
        role: m.role,
        content: m.content,
        sources: Array.isArray(m.sources) ? m.sources : null, // restore citation block
        streaming: false,
      })))
    } catch {
      setMessages([])
    } finally {
      setLoadingMessages(false)
    }
  }

  const newChat = () => {
    if (loading) return
    setCurrentSessionId(null)
    setMessages([])
    setInput('')
  }

  const deleteSession = async (sid, e) => {
    e?.stopPropagation()
    if (!confirm('Delete this chat session?')) return
    try {
      await api.deleteChatSession(sid)
      if (sid === currentSessionId) {
        setCurrentSessionId(null)
        setMessages([])
      }
      refreshSessions()
    } catch (err) {
      alert('Failed to delete: ' + err.message)
    }
  }

  const sendMessage = async (textArg, forceTicker = null) => {
    const q = (textArg ?? input).trim()
    if (!q || loading) return

    if (textArg == null) setInput('')
    setMessages(prev => [...prev, { role: 'user', content: q }])
    setLoading(true)

    const msgIndex = (prev => prev.length + 1)(messages)
    setMessages(prev => [...prev, { role: 'assistant', content: '', thinking: '', status: '', streaming: true }])

    let accumulated = ''
    let thinkingText = ''
    let thinkingStart = null
    let thinkingDuration = null

    await api.chatStream(q, {
      forceTicker,
      contextTickers: watchlist,
      sessionId: currentSessionId,
      onSession: ({ id, is_new }) => {
        if (id && id !== currentSessionId) setCurrentSessionId(id)
        if (is_new) refreshSessions()
      },
      onSources: (items) => {
        // Citation payload arrives once near the end of the stream. Attach
        // it to the in-flight assistant message so SourcesBlock can render.
        setMessages(prev => {
          const updated = [...prev]
          if (updated[msgIndex]) {
            updated[msgIndex] = { ...updated[msgIndex], sources: items }
          }
          return updated
        })
      },
      onStatus: (text) => {
        setMessages(prev => {
          const updated = [...prev]
          updated[msgIndex] = { ...updated[msgIndex], status: text }
          return updated
        })
      },
      onThinking: (text) => {
        if (!thinkingStart) thinkingStart = Date.now()
        thinkingText += text
        setMessages(prev => {
          const updated = [...prev]
          updated[msgIndex] = { ...updated[msgIndex], thinking: thinkingText }
          return updated
        })
      },
      onToken: (token) => {
        if (thinkingStart && thinkingDuration === null) {
          thinkingDuration = Math.round((Date.now() - thinkingStart) / 1000)
          setMessages(prev => {
            const updated = [...prev]
            updated[msgIndex] = { ...updated[msgIndex], thinkingTime: thinkingDuration }
            return updated
          })
        }
        accumulated += token
        setMessages(prev => {
          const updated = [...prev]
          updated[msgIndex] = { ...updated[msgIndex], content: accumulated }
          return updated
        })
      },
      onClarification: ({ question, options }) => {
        setMessages(prev => {
          const updated = [...prev]
          updated[msgIndex] = {
            role: 'assistant',
            type: 'clarification',
            originalQuery: question,
            options: options || [],
            streaming: false,
          }
          return updated
        })
        setLoading(false)
      },
      onError: (errMsg) => {
        accumulated = accumulated || `Error: ${errMsg}`
        setMessages(prev => {
          const updated = [...prev]
          updated[msgIndex] = { ...updated[msgIndex], content: accumulated }
          return updated
        })
      },
      onDone: () => {
        setMessages(prev => {
          const updated = [...prev]
          if (updated[msgIndex]?.type === 'clarification') return updated
          updated[msgIndex] = {
            ...updated[msgIndex],
            content: accumulated || 'The AI model is busy. Please wait a moment and try again.',
            streaming: false,
            elapsed: timerRef.elapsed,
            thinkingTime: thinkingDuration,
          }
          return updated
        })
        setLoading(false)
        refreshSessions()
      },
    })
  }

  const handleClarificationClick = (originalQuery, ticker) => {
    sendMessage(`${originalQuery} (focus: $${ticker})`, ticker)
  }

  return (
    <div style={{ display: 'flex', height: '100%', gap: 12 }}>
      {/* Sessions list — portaled into Sidebar's slot when available. */}
      {sidebarSlot && createPortal(
        <div className="sb-sessions">
          <button
            type="button"
            className="sb-new-chat"
            onClick={newChat}
            disabled={loading}
          >
            <Icon name="plus" size={14} />
            <span>New chat</span>
          </button>

          <div className="sb-sessions-title">Recent</div>

          <div className="sb-sessions-list">
            {sessions.length === 0 ? (
              <div className="sb-sessions-empty">No chats yet</div>
            ) : sessions.map((s) => (
              <div
                key={s.id}
                onClick={() => loadSession(s.id)}
                className={`sb-session${s.id === currentSessionId ? ' active' : ''}`}
              >
                <div className="sb-session-main">
                  <div className="sb-session-title">{s.title || 'Untitled'}</div>
                  <div className="sb-session-meta">
                    {relativeTime(s.last_message_at)} · {s.message_count || 0}
                  </div>
                </div>
                <button
                  onClick={(e) => deleteSession(s.id, e)}
                  title="Delete"
                  aria-label="Delete chat"
                  className="sb-session-del"
                ><Icon name="trash" size={12} /></button>
              </div>
            ))}
          </div>
        </div>,
        sidebarSlot
      )}

      {/* Right panel: chat */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', maxWidth: 800, minWidth: 0 }}>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 700, marginBottom: 16 }}>Chat</h1>

        <div style={{
          flex: 1,
          overflow: 'auto',
          background: '#18181b',
          borderRadius: 12,
          border: '1px solid #27272a',
          padding: 16,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
          marginBottom: 16,
        }}>
          {loadingMessages && (
            <div style={{ color: '#52525b', textAlign: 'center', marginTop: 40, fontSize: '0.85rem' }}>
              Loading messages…
            </div>
          )}
          {!loadingMessages && messages.length === 0 && (
            <div style={{ color: '#52525b', textAlign: 'center', marginTop: 80, fontSize: '0.9rem' }}>
              {watchlist.length > 0
                ? `Ask anything about ${watchlist.slice(0, 3).join(', ')}${watchlist.length > 3 ? '…' : ''} or the market.`
                : 'Ask a question. Tip: add tickers to your Watchlist so we can auto-scope follow-ups.'}
            </div>
          )}
          {messages.map((msg, i) => (
            msg.streaming && !msg.content && !msg.thinking ? null :
              <div key={i} style={{
                alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
                maxWidth: '85%',
                background: msg.role === 'user' ? '#1d4ed8' : '#27272a',
                padding: '10px 14px',
                borderRadius: 12,
                fontSize: '0.875rem',
                lineHeight: 1.6,
              }}>
                {msg.role === 'assistant' && msg.type === 'clarification' ? (
                  <div>
                    <div style={{ marginBottom: 10, color: '#d4d4d8' }}>
                      Which ticker should I focus on?
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {msg.options.map(t => (
                        <button
                          key={t}
                          onClick={() => handleClarificationClick(msg.originalQuery, t)}
                          style={{
                            padding: '6px 12px',
                            fontSize: '0.8rem',
                            background: '#3f3f46',
                            color: '#fff',
                            border: '1px solid #52525b',
                            borderRadius: 999,
                            cursor: 'pointer',
                          }}
                        >
                          ${t}
                        </button>
                      ))}
                    </div>
                  </div>
                ) : (
                  <>
                    {msg.role === 'assistant' && msg.thinking && (
                      <details open={msg.streaming && !msg.content} style={{ marginBottom: msg.content ? 8 : 0 }}>
                        <summary style={{ cursor: 'pointer', userSelect: 'none', fontSize: '0.8rem', color: '#a1a1aa', listStyle: 'none' }}>
                          🧠 {msg.streaming && !msg.content
                            ? <><span className="thinking-dot" style={{ marginLeft: 4, marginRight: 6 }} />Thinking... ({elapsed}s)</>
                            : `Thought for ${msg.thinkingTime || '?'}s`
                          }
                        </summary>
                        <div
                          ref={el => { if (el && msg.streaming) el.scrollTop = el.scrollHeight }}
                          style={{
                            marginTop: 4,
                            padding: '8px 12px',
                            background: '#1a1a1e',
                            borderRadius: 8,
                            maxHeight: 200,
                            overflow: 'auto',
                            whiteSpace: 'pre-wrap',
                            fontSize: '0.78rem',
                            color: '#71717a',
                            fontStyle: 'italic',
                          }}>
                          {msg.thinking}
                        </div>
                      </details>
                    )}
                    <div className={msg.role === 'assistant' ? 'markdown-body' : ''}>
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                    </div>
                    {msg.role === 'assistant' && msg.elapsed != null && (
                      <div style={{ marginTop: 6, fontSize: '0.7rem', color: '#52525b' }}>
                        ⏱ Took {msg.elapsed}s
                      </div>
                    )}
                    {msg.role === 'assistant' && !msg.streaming && Array.isArray(msg.sources) && msg.sources.length > 0 && (
                      <SourcesBlock items={msg.sources} />
                    )}
                  </>
                )}
              </div>
          ))}
          {loading && messages.some(m => m.streaming && !m.content && !m.thinking) && (
            <div style={{ color: '#71717a', fontSize: '0.8rem', padding: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className="thinking-dot" />
              {messages.find(m => m.streaming)?.status || 'Processing...'} ({elapsed}s)
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <ChatComposer
          value={input}
          onChange={setInput}
          onSubmit={() => sendMessage()}
          disabled={loading}
          sessionId={currentSessionId}
          attachedFiles={attachedFiles}
          onAttach={(rec) => { setAttachedFiles((list) => [...list, rec]); setFilesRefreshKey((k) => k + 1) }}
          onDetach={(rec) => setAttachedFiles((list) => list.filter((x) => x.id !== rec.id))}
          onPreview={(rec) => setPreviewFile(rec)}
        />
      </div>

      <FilesPanel
        refreshKey={filesRefreshKey}
        onPreview={(rec) => setPreviewFile(rec)}
        onRemove={(rec) => setAttachedFiles((list) => list.filter((x) => x.id !== rec.id))}
      />
      {previewFile && (
        <FilePreviewModal
          file={previewFile}
          onClose={() => setPreviewFile(null)}
        />
      )}
    </div>
  )
}
