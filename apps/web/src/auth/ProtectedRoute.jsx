import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from './AuthContext'

export default function ProtectedRoute({ children }) {
    const { session, loading } = useAuth()
    const location = useLocation()

    if (loading) {
        return (
            <div style={{ padding: 32, color: '#a1a1aa' }}>Loading…</div>
        )
    }
    if (!session) {
        return <Navigate to="/login" replace state={{ from: location }} />
    }
    return children
}
