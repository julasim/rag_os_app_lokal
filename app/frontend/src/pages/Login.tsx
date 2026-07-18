import { useState, FormEvent } from 'react';
import { useNavigate, Navigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

export default function Login() {
  const { login, isLoggedIn } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (isLoggedIn) {
    return <Navigate to="/dashboard" replace />;
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(email, password);
      navigate('/dashboard');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Anmeldung fehlgeschlagen');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        background: '#fafafa',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
      }}
    >
      <div style={{ width: 380, display: 'flex', flexDirection: 'column', gap: 24 }}>
        {/* Brand */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div
            style={{
              width: 32,
              height: 32,
              background: '#111',
              borderRadius: 8,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
            }}
          >
            <span style={{ color: '#fff', fontWeight: 700, fontSize: 16, lineHeight: 1 }}>R</span>
          </div>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: '#111', lineHeight: 1.2 }}>
              RAG OS
            </div>
            <div style={{ fontSize: 12, color: '#a3a3a3', lineHeight: 1.2 }}>
              rag-os.sima.business
            </div>
          </div>
        </div>

        {/* Login Panel */}
        <div
          style={{
            background: '#ffffff',
            border: '1px solid #ededed',
            borderRadius: 8,
            padding: 24,
          }}
        >
          <h1
            style={{
              margin: '0 0 6px',
              fontSize: 24,
              fontWeight: 600,
              color: '#111',
              letterSpacing: '-0.5px',
            }}
          >
            Anmelden
          </h1>
          <p style={{ margin: '0 0 20px', fontSize: 13, color: '#737373' }}>
            Admin-Zugang zum Wissens-Knoten
          </p>

          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <label
                style={{
                  display: 'block',
                  fontSize: 12,
                  fontWeight: 500,
                  color: '#262626',
                  marginBottom: 4,
                }}
              >
                E-Mail
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                placeholder="admin@sima.business"
                className="w-full px-2.5 py-1.5 border border-[#ededed] rounded-md bg-white text-sm text-[#111] outline-none focus:border-[#111] transition-colors"
                style={{ display: 'block', width: '100%', boxSizing: 'border-box' }}
              />
            </div>

            <div>
              <label
                style={{
                  display: 'block',
                  fontSize: 12,
                  fontWeight: 500,
                  color: '#262626',
                  marginBottom: 4,
                }}
              >
                Passwort
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                placeholder="••••••••"
                className="w-full px-2.5 py-1.5 border border-[#ededed] rounded-md bg-white text-sm text-[#111] outline-none focus:border-[#111] transition-colors"
                style={{ display: 'block', width: '100%', boxSizing: 'border-box' }}
              />
            </div>

            {error && (
              <div
                style={{
                  padding: '8px 12px',
                  background: '#fef2f2',
                  border: '1px solid #fecaca',
                  borderRadius: 6,
                  fontSize: 13,
                  color: '#991b1b',
                }}
              >
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="px-3 py-1.5 bg-[#111] text-white text-xs font-medium rounded-md border border-[#111] cursor-pointer hover:bg-[#262626] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              style={{ width: '100%', padding: '10px', fontSize: 14, marginTop: 4 }}
            >
              {loading ? 'Anmelden…' : 'Anmelden →'}
            </button>
          </form>
        </div>

        {/* Status Box */}
        <div
          style={{
            background: '#ffffff',
            border: '1px solid #ededed',
            borderRadius: 8,
            padding: '12px 16px',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: '#10b981',
              flexShrink: 0,
            }}
          />
          <div>
            <div style={{ fontSize: 12, fontWeight: 500, color: '#047857' }}>
              Alle Dienste betriebsbereit
            </div>
            <div style={{ fontSize: 11, color: '#a3a3a3', marginTop: 1 }}>
              Postgres · Qdrant · Ollama · API
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
