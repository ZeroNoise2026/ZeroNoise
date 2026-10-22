import { useState, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

// Auto-detect popular providers so we can offer a deep-link button.
// Pattern lifted from Vercel / Linear / Notion signup flows.
function emailProvider(email) {
    const domain = (email.split('@')[1] || '').toLowerCase()
    if (/gmail\.com$|googlemail\.com$/.test(domain)) return { name: 'Gmail', url: 'https://mail.google.com' }
    if (/outlook\.|hotmail\.|live\.|msn\./.test(domain)) return { name: 'Outlook', url: 'https://outlook.live.com/mail' }
    if (/yahoo\./.test(domain)) return { name: 'Yahoo Mail', url: 'https://mail.yahoo.com' }
    if (/icloud\.|me\.com$|mac\.com$/.test(domain)) return { name: 'iCloud Mail', url: 'https://www.icloud.com/mail' }
    if (/proton(mail)?\./.test(domain)) return { name: 'Proton Mail', url: 'https://mail.proton.me' }
    if (/qq\.com$/.test(domain)) return { name: 'QQ Mail', url: 'https://mail.qq.com' }
    if (/163\.com$|126\.com$|yeah\.net$/.test(domain)) return { name: '163 Mail', url: 'https://mail.163.com' }
    return null
}

export default function SignupPage() {
    const { signUp, resendConfirmation } = useAuth()
    const navigate = useNavigate()

    const [email, setEmail] = useState('')
    const [password, setPassword] = useState('')
    const [error, setError] = useState('')
    const [loading, setLoading] = useState(false)

    const [submitted, setSubmitted] = useState(false)
    const [alreadyRegistered, setAlreadyRegistered] = useState(false)
    const [resendCooldown, setResendCooldown] = useState(0)
    const [resendMsg, setResendMsg] = useState('')

    useEffect(() => {
        if (resendCooldown <= 0) return
        const t = setTimeout(() => setResendCooldown((c) => c - 1), 1000)
        return () => clearTimeout(t)
    }, [resendCooldown])

    const onSubmit = async (e) => {
        e.preventDefault()
        setError('')
        if (password.length < 6) {
            setError('Password must be at least 6 characters.')
            return
        }
        setLoading(true)
        try {
            const data = await signUp(email.trim(), password)
            if (data.session) {
                navigate('/briefing', { replace: true })
                return
            }
            setAlreadyRegistered(!!data.alreadyRegistered)
            setSubmitted(true)
            setResendCooldown(30)
        } catch (err) {
            setError(err.message || 'Signup failed')
        } finally {
            setLoading(false)
        }
    }

    const onResend = async () => {
        if (resendCooldown > 0) return
        setResendMsg('')
        try {
            await resendConfirmation(email.trim())
            setResendMsg('Email re-sent.')
            setResendCooldown(30)
        } catch (err) {
            setResendMsg(err.message || 'Could not resend.')
        }
    }

    if (submitted) {
        const provider = emailProvider(email)
        return (
            <div style={styles.shell}>
                <div style={styles.card}>
                    <div style={styles.iconWrap}>
                        <div style={styles.iconCircle}>✉️</div>
                    </div>

                    <h1 style={styles.successTitle}>
                        {alreadyRegistered ? 'Almost there' : 'Check your email'}
                    </h1>

                    <p style={styles.successBody}>
                        {alreadyRegistered ? (
                            <>
                                An account with <strong style={styles.emailEm}>{email}</strong> already
                                exists. If it&rsquo;s yours, sign in below. If you never finished
                                confirming, we just sent another link.
                            </>
                        ) : (
                            <>
                                We sent a confirmation link to{' '}
                                <strong style={styles.emailEm}>{email}</strong>.
                                Click the link to activate your account, then come back to sign in.
                            </>
                        )}
                    </p>

                    {provider && (
                        <a
                            href={provider.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={styles.primaryBtn}
                        >
                            Open {provider.name}
                        </a>
                    )}

                    <Link to="/login" style={styles.secondaryBtn}>
                        Back to sign in
                    </Link>

                    <div style={styles.hint}>
                        Didn&rsquo;t get the email? Check your spam folder, or{' '}
                        <button
                            type="button"
                            onClick={onResend}
                            disabled={resendCooldown > 0}
                            style={styles.linkBtn}
                        >
                            {resendCooldown > 0 ? `resend in ${resendCooldown}s` : 'resend it'}
                        </button>
                        .
                    </div>
                    {resendMsg && <div style={styles.info}>{resendMsg}</div>}

                    <button
                        type="button"
                        onClick={() => {
                            setSubmitted(false)
                            setAlreadyRegistered(false)
                        }}
                        style={styles.linkBtnMuted}
                    >
                        Use a different email
                    </button>
                </div>
            </div>
        )
    }

    return (
        <div style={styles.shell}>
            <form onSubmit={onSubmit} style={styles.card}>
                <h1 style={styles.title}>QuantAgent</h1>
                <p style={styles.subtitle}>Create an account</p>

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

                <label style={styles.label}>Password (min 6 chars)</label>
                <input
                    type="password"
                    autoComplete="new-password"
                    required
                    minLength={6}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    style={styles.input}
                />

                <button type="submit" disabled={loading} style={styles.button}>
                    {loading ? 'Creating…' : 'Sign up'}
                </button>

                <p style={styles.footer}>
                    Already have an account? <Link to="/login">Sign in</Link>
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
        maxWidth: 400,
        background: '#18181b',
        border: '1px solid #27272a',
        borderRadius: 12,
        padding: 32,
        display: 'flex',
        flexDirection: 'column',
    },
    title: { color: '#3b82f6', fontSize: '1.5rem', fontWeight: 700, marginBottom: 4 },
    subtitle: { color: '#a1a1aa', fontSize: '0.875rem', marginBottom: 20 },

    iconWrap: { display: 'flex', justifyContent: 'center', marginBottom: 18 },
    iconCircle: {
        width: 64,
        height: 64,
        borderRadius: '50%',
        background: '#1e3a8a',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: 30,
    },
    successTitle: {
        color: '#fafafa',
        fontSize: '1.35rem',
        fontWeight: 700,
        textAlign: 'center',
        marginBottom: 10,
    },
    successBody: {
        color: '#a1a1aa',
        fontSize: '0.9rem',
        lineHeight: 1.55,
        textAlign: 'center',
        marginBottom: 22,
    },
    emailEm: { color: '#e4e4e7', fontWeight: 600 },
    primaryBtn: {
        display: 'block',
        textAlign: 'center',
        background: '#3b82f6',
        color: '#fff',
        padding: '11px 16px',
        borderRadius: 8,
        fontWeight: 600,
        fontSize: '0.9rem',
        marginBottom: 10,
        textDecoration: 'none',
    },
    secondaryBtn: {
        display: 'block',
        textAlign: 'center',
        background: 'transparent',
        color: '#e4e4e7',
        padding: '10px 16px',
        borderRadius: 8,
        border: '1px solid #3f3f46',
        fontWeight: 500,
        fontSize: '0.9rem',
        marginBottom: 18,
        textDecoration: 'none',
    },
    hint: {
        color: '#71717a',
        fontSize: '0.78rem',
        lineHeight: 1.55,
        textAlign: 'center',
        marginTop: 6,
        marginBottom: 6,
    },
    linkBtn: {
        background: 'transparent',
        color: '#60a5fa',
        padding: 0,
        border: 'none',
        fontSize: '0.78rem',
        textDecoration: 'underline',
        cursor: 'pointer',
    },
    linkBtnMuted: {
        background: 'transparent',
        color: '#71717a',
        padding: '12px 0 0',
        border: 'none',
        fontSize: '0.78rem',
        textAlign: 'center',
        marginTop: 4,
    },

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
    info: {
        background: '#14532d',
        color: '#bbf7d0',
        padding: '8px 12px',
        borderRadius: 8,
        fontSize: '0.78rem',
        marginTop: 8,
        textAlign: 'center',
    },
}
