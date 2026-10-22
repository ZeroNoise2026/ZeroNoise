import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { supabase } from '../auth/supabaseClient'

/**
 * Lands here from the Supabase confirmation email link.
 * supabase-js (`detectSessionInUrl: true`) auto-consumes the hash/query and
 * persists the session. We just wait for it, then route.
 *
 * On error (expired link, mismatched redirect URL, etc.) we forward the
 * error message to /login so the user sees it instead of a blank page.
 */
export default function AuthCallbackPage() {
    const navigate = useNavigate()
    const [status, setStatus] = useState('Confirming your email…')

    useEffect(() => {
        // Supabase appends errors as query params (?error=...&error_description=...)
        const url = new URL(window.location.href)
        const errParam =
            url.searchParams.get('error_description') ||
            url.searchParams.get('error') ||
            // Hash-style errors: #error=...&error_description=...
            new URLSearchParams(window.location.hash.replace(/^#/, '')).get(
                'error_description'
            )

        if (errParam) {
            navigate(
                `/login?error=${encodeURIComponent(errParam)}`,
                { replace: true }
            )
            return
        }

        // Give supabase-js a tick to process the URL hash and emit SIGNED_IN.
        let cancelled = false
        const finish = async () => {
            // Small delay so detectSessionInUrl can run
            await new Promise((r) => setTimeout(r, 50))
            const { data } = await supabase.auth.getSession()
            if (cancelled) return
            if (data.session) {
                navigate('/briefing', { replace: true })
            } else {
                // No session — confirmation worked but no auto-login (rare).
                // Send them to login with a friendly note.
                navigate(
                    '/login?confirmed=1',
                    { replace: true }
                )
            }
        }
        finish()
        // Also react to SIGNED_IN explicitly in case getSession is too early
        const { data: sub } = supabase.auth.onAuthStateChange((event, s) => {
            if (event === 'SIGNED_IN' && s) {
                navigate('/briefing', { replace: true })
            }
        })
        return () => {
            cancelled = true
            sub.subscription.unsubscribe()
        }
    }, [navigate])

    return (
        <div style={styles.shell}>
            <div style={styles.card}>
                <div style={styles.spinner} />
                <p style={styles.text}>{status}</p>
            </div>
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
    },
    card: {
        background: '#18181b',
        border: '1px solid #27272a',
        borderRadius: 12,
        padding: 32,
        textAlign: 'center',
    },
    text: { color: '#d4d4d8', fontSize: '0.9rem', marginTop: 12 },
    spinner: {
        width: 28,
        height: 28,
        border: '3px solid #27272a',
        borderTopColor: '#3b82f6',
        borderRadius: '50%',
        margin: '0 auto',
        animation: 'spin 0.8s linear infinite',
    },
}
