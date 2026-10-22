import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { supabase } from './supabaseClient'

const AuthContext = createContext({
    session: null,
    user: null,
    loading: true,
    signIn: async () => { },
    signUp: async () => { },
    signOut: async () => { },
})

export function AuthProvider({ children }) {
    const [session, setSession] = useState(null)
    const [loading, setLoading] = useState(true)

    useEffect(() => {
        // Pull the cached session on first mount (synchronous-ish; supabase-js
        // hydrates from localStorage internally).
        supabase.auth.getSession().then(({ data }) => {
            setSession(data.session ?? null)
            setLoading(false)
        })
        const { data: sub } = supabase.auth.onAuthStateChange((_event, s) => {
            setSession(s)
        })
        return () => sub.subscription.unsubscribe()
    }, [])

    const signIn = useCallback(async (email, password) => {
        const { data, error } = await supabase.auth.signInWithPassword({ email, password })
        if (error) throw error
        return data
    }, [])

    const signUp = useCallback(async (email, password) => {
        const { data, error } = await supabase.auth.signUp({
            email,
            password,
            options: {
                emailRedirectTo: `${window.location.origin}/auth/callback`,
            },
        })
        if (error) throw error
        // Supabase silently returns a "user" with empty identities[] when the
        // email is already registered (to prevent enumeration). Surface it.
        const alreadyRegistered =
            !data.session &&
            data.user &&
            Array.isArray(data.user.identities) &&
            data.user.identities.length === 0
        return { ...data, alreadyRegistered }
    }, [])

    const resendConfirmation = useCallback(async (email) => {
        const { error } = await supabase.auth.resend({
            type: 'signup',
            email,
            options: {
                emailRedirectTo: `${window.location.origin}/auth/callback`,
            },
        })
        if (error) throw error
    }, [])

    const signOut = useCallback(async () => {
        await supabase.auth.signOut()
        setSession(null)
    }, [])

    const value = {
        session,
        user: session?.user ?? null,
        loading,
        signIn,
        signUp,
        signOut,
        resendConfirmation,
    }
    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
    return useContext(AuthContext)
}
