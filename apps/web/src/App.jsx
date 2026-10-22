import { useState, useEffect } from 'react'
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import WatchlistPage from './pages/WatchlistPage'
import BriefingPage from './pages/BriefingPage'
import ChatPage from './pages/ChatPage'
import LoginPage from './pages/LoginPage'
import SignupPage from './pages/SignupPage'
import AuthCallbackPage from './pages/AuthCallbackPage'
import ProtectedRoute from './auth/ProtectedRoute'
import { useAuth } from './auth/AuthContext'

function AppShell({ children }) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()
  const { signOut, user } = useAuth()

  // Map pathname → sidebar active id
  const activePage =
    location.pathname.startsWith('/watchlist') ? 'watchlist' :
      location.pathname.startsWith('/chat') ? 'chat' :
        'briefing'

  const handleNavigate = (id) => {
    navigate(`/${id}`)
    setSidebarOpen(false)
  }

  const handleSignOut = async () => {
    await signOut()
    navigate('/login', { replace: true })
  }

  useEffect(() => {
    const onResize = () => {
      if (window.innerWidth > 768) setSidebarOpen(false)
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  return (
    <div className="app-shell">
      <button
        className="app-hamburger"
        aria-label="Toggle navigation"
        onClick={() => setSidebarOpen((s) => !s)}
      >
        {sidebarOpen ? '\u2715' : '\u2630'}
      </button>
      <div
        className={`app-overlay ${sidebarOpen ? 'show' : ''}`}
        onClick={() => setSidebarOpen(false)}
      />
      <Sidebar
        activePage={activePage}
        onNavigate={handleNavigate}
        open={sidebarOpen}
        userEmail={user?.email}
        onSignOut={handleSignOut}
      />
      <main className="app-main">{children}</main>
    </div>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
      <Route path="/auth/callback" element={<AuthCallbackPage />} />
      <Route
        path="/briefing"
        element={<ProtectedRoute><AppShell><BriefingPage /></AppShell></ProtectedRoute>}
      />
      <Route
        path="/watchlist"
        element={<ProtectedRoute><AppShell><WatchlistPage /></AppShell></ProtectedRoute>}
      />
      <Route
        path="/chat"
        element={<ProtectedRoute><AppShell><ChatPage /></AppShell></ProtectedRoute>}
      />
      <Route path="*" element={<Navigate to="/briefing" replace />} />
    </Routes>
  )
}
