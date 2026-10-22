import { useState, useEffect } from 'react'
import { Link, useNavigate, useLocation, useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export default function LoginPage() {
    const { signIn, session } = useAuth()
    const navigate = useNavigate()
    const location = useLocation()
    const [searchParams, setSearchParams] = useSearchParams()
    const [email, setEmail] = useState('')
    const [password, setPassword] = useState('')
    const [error, setError] = useState('')
    const [notice, setNotice] = useState('')
    const [loading, setLoading] = useState(false)

    // Already signed in? bounce to app.
    useEffect(() => {
        if (session) {
            const dest = location.state?.from?.pathname || '/briefing'
            navigate(dest, { replace: true })
        }
    }, [session, navigate, location.state])

    // Surface errors / confirmation status forwarded from /auth/callback
    useEffect(() => {
        const err = searchParams.get('error')
        const confirmed = searchParams.get('confirmed')
        if (err) setError(decodeURIComponent(err))
        else if (confirmed) setNotice('Email confirmed. Please sign in.')
        if (err || confirmed) {
            const next = new URLSearchParams(searchParams)
            next.delete('error')
            next.delete('confirmed')
            setSearchParams(next, { replace: true })
        }
    }, [searchParams, setSearchParams])

    const onSubmit = async (e) => {
        e.preventDefault()
        setError('')
        setLoading(true)
        try {
            await signIn(email.trim(), password)
            const dest = location.state?.from?.pathname || '/briefing'
            navigate(dest, { replace: true })
        } catch (err) {
            setError(err.message || 'Login failed')
        } finally {
            setLoading(false)
        }
    }

    return (
        <div style={styles.shell}>
            <form onSubmit={onSubmit} style={styles.card}>
                <h1 style={styles.title}>QuantAgent</h1>
                <p style={styles.subtitle}>Sign in to your account</p>

                {notice && <div style={styles.notice}>{notice}</div>}
                {error && <div style={styles.error}>{error}</div>}

                <label style={styles.label}>Email</label>
                <input
                    type="email"
                    autoComplete="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    style={styles.input}
                />

                <label style={styles.label}>Password</label>
                <input
                    type="password"
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    style={styles.input}
                />

                <button type="submit" disabled={loading} style={styles.button}>
                    {loading ? 'Signing in…' : 'Sign in'}
                </button>

                <p style={styles.footer}>
                    No account? <Link to="/signup">Sign up</Link>
                </p>
            </form>
        </div>
    )
}

const styles = {
    shell: {
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#0f1117',
        padding: 16,
    },
    card: {
        width: '100%',
        maxWidth: 380,
        background: '#18181b',
        border: '1px solid #27272a',
        borderRadius: 12,
        padding: 28,
        display: 'flex',
        flexDirection: 'column',
    },
    title: { color: '#3b82f6', fontSize: '1.5rem', fontWeight: 700, marginBottom: 4 },
    subtitle: { color: '#a1a1aa', fontSize: '0.875rem', marginBottom: 20 },
    label: { color: '#d4d4d8', fontSize: '0.8rem', marginTop: 12, marginBottom: 6 },
    input: {
        background: '#0f1117',
        border: '1px solid #3f3f46',
        color: '#e4e4e7',
        borderRadius: 8,
        padding: '9px 12px',
        fontSize: '0.875rem',
    },
    button: {
        marginTop: 20,
        background: '#3b82f6',
        color: '#fff',
        padding: '10px 16px',
        fontWeight: 600,
    },
    footer: { color: '#a1a1aa', fontSize: '0.8rem', marginTop: 16, textAlign: 'center' },
    error: {
        background: '#7f1d1d',
        color: '#fecaca',
        padding: '8px 12px',
        borderRadius: 8,
        fontSize: '0.8rem',
        marginBottom: 8,
    },
    notice: {
        background: '#14532d',
        color: '#bbf7d0',
        padding: '8px 12px',
        borderRadius: 8,
        fontSize: '0.8rem',
        marginBottom: 8,
    },
}
