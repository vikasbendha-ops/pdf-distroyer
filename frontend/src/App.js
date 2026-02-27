import React, { createContext, useContext, useState, useCallback, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { Toaster } from './components/ui/sonner';
import { LanguageProvider } from './contexts/LanguageContext';
import { supabase } from './lib/supabase';
import { getProfile, updateLanguage } from './lib/api';

import Landing from './pages/Landing';
import Login from './pages/Login';
import Register from './pages/Register';
import Dashboard from './pages/Dashboard';
import PDFManagement from './pages/PDFManagement';
import LinkGenerator from './pages/LinkGenerator';
import MyLinks from './pages/MyLinks';
import SecureViewer from './pages/SecureViewer';
import ExpiredPage from './pages/ExpiredPage';
import Pricing from './pages/Pricing';
import Settings from './pages/Settings';
import AdminDashboard from './pages/AdminDashboard';
import AdminSettings from './pages/AdminSettings';
import AdminUsers from './pages/AdminUsers';
import AdminLinks from './pages/AdminLinks';

const AuthContext = createContext(null);

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
};

const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadProfile = useCallback(async () => {
    try {
      const profile = await getProfile();
      setUser(profile);
      if (profile?.language) {
        localStorage.setItem('preferredLanguage', profile.language);
      }
    } catch {
      setUser(null);
    }
  }, []);

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (session) {
        loadProfile().finally(() => setLoading(false));
      } else {
        setLoading(false);
      }
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      if (event === 'SIGNED_IN' && session) {
        loadProfile();
      } else if (event === 'SIGNED_OUT') {
        setUser(null);
      }
    });

    return () => subscription.unsubscribe();
  }, [loadProfile]);

  const login = async (email, password) => {
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) throw error;
    const profile = await getProfile();
    setUser(profile);
    if (profile?.language) localStorage.setItem('preferredLanguage', profile.language);
    return profile;
  };

  const register = async (name, email, password, language = 'en') => {
    const { error } = await supabase.auth.signUp({
      email,
      password,
      options: { data: { name, language } }
    });
    if (error) throw error;
    const profile = await getProfile();
    setUser(profile);
    localStorage.setItem('preferredLanguage', language);
    return profile;
  };

  const logout = async () => {
    await supabase.auth.signOut();
    setUser(null);
  };

  const refreshUser = async () => {
    try {
      const profile = await getProfile();
      setUser(profile);
    } catch {}
  };

  const updateUserLanguage = async (language) => {
    await updateLanguage(language);
    setUser(prev => ({ ...prev, language }));
    localStorage.setItem('preferredLanguage', language);
  };

  return (
    <AuthContext.Provider value={{ user, setUser, login, register, logout, loading, refreshUser, updateUserLanguage }}>
      {children}
    </AuthContext.Provider>
  );
};

const ProtectedRoute = ({ children, adminOnly = false }) => {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-stone-50">
        <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-emerald-900"></div>
      </div>
    );
  }

  if (!user) return <Navigate to="/login" state={{ from: location }} replace />;
  if (adminOnly && user.role !== 'admin') return <Navigate to="/dashboard" replace />;

  return children;
};

function AppRouter() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/pricing" element={<Pricing />} />
      <Route path="/view/:token" element={<SecureViewer />} />
      <Route path="/expired" element={<ExpiredPage />} />

      <Route path="/dashboard" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
      <Route path="/pdfs" element={<ProtectedRoute><PDFManagement /></ProtectedRoute>} />
      <Route path="/links" element={<ProtectedRoute><MyLinks /></ProtectedRoute>} />
      <Route path="/links/create" element={<ProtectedRoute><LinkGenerator /></ProtectedRoute>} />
      <Route path="/settings" element={<ProtectedRoute><Settings /></ProtectedRoute>} />

      <Route path="/admin" element={<ProtectedRoute adminOnly><AdminDashboard /></ProtectedRoute>} />
      <Route path="/admin/users" element={<ProtectedRoute adminOnly><AdminUsers /></ProtectedRoute>} />
      <Route path="/admin/links" element={<ProtectedRoute adminOnly><AdminLinks /></ProtectedRoute>} />
      <Route path="/admin/settings" element={<ProtectedRoute adminOnly><AdminSettings /></ProtectedRoute>} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

function App() {
  return (
    <BrowserRouter>
      <LanguageProvider>
        <AuthProvider>
          <div className="App">
            <div className="noise-overlay" />
            <AppRouter />
            <Toaster position="top-right" richColors />
          </div>
        </AuthProvider>
      </LanguageProvider>
    </BrowserRouter>
  );
}

export default App;
