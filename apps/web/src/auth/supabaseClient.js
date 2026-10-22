// Single Supabase client instance for the whole app.
// Used by AuthContext for login/logout and by api.js for fetching the
// current access_token to send to the backend.
import { createClient } from '@supabase/supabase-js'

const url = import.meta.env.VITE_SUPABASE_URL
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY

if (!url || !key) {
    // Fail loud at dev time rather than producing confusing 401s later.
    // eslint-disable-next-line no-console
    console.error(
        '[supabase] Missing VITE_SUPABASE_URL or VITE_SUPABASE_PUBLISHABLE_KEY. ' +
        'Copy frontend/.env.example to frontend/.env.local and fill in.',
    )
}

export const supabase = createClient(url, key, {
    auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
    },
})
