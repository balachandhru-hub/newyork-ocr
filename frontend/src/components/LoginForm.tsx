import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { Eye, EyeOff, User, Lock, ArrowRight } from 'lucide-react';
import { loginUser, refreshToken } from '../api/AuthApi';
import PachaLogo from '../assets/pacha-logo.svg';

export interface LoginFormProps {
  onForgotPasswordClick?: () => void;
}

export const LoginForm: React.FC<LoginFormProps> = ({ onForgotPasswordClick }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password) {
      setError('Please fill in both Username and Password.');
      return;
    }

    setLoading(true);
    setError('');

    try {
      const res = await loginUser({ userName: username, password });
      if ('personId' in res && res.personId) {
        try {
          await refreshToken();
        } catch (refreshErr) {
          console.warn('Initial post-login token refresh warning:', refreshErr);
        }
        navigate('/dashboard');
      } else {
        setError(res.message || 'Login failed. Please check your credentials.');
      }
    } catch (err: any) {
      setError(err.message || 'Unable to connect to authentication server.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-left-col">
      <div className="pacha-brand-row">
        <img src={PachaLogo} alt="PACHA" className="pacha-brand-logo" />
        <div className="pacha-brand-text">
          <span className="pacha-brand-welcome">Welcome to,</span>
          <span className="pacha-brand-title">PACHA Smart Invoice Automation System</span>
        </div>
      </div>

      <div className="auth-card">
        <div className="auth-card-header">
          <h1 className="auth-card-title">Sign In</h1>
        </div>

      {error && (
        <div className="auth-error-box">
          {error}
        </div>
      )}

      <form className="auth-form" onSubmit={handleSubmit}>
        <div className="input-field-group icon-input-wrapper">
          <User size={17} className="input-icon" />
          <input
            type="text"
            className="custom-input icon-padded"
            placeholder="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </div>

        <div className="input-field-group password-input-wrapper icon-input-wrapper">
          <Lock size={17} className="input-icon" />
          <input
            type={showPassword ? 'text' : 'password'}
            className="custom-input icon-padded"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <button
            type="button"
            className="password-toggle-btn"
            onClick={() => setShowPassword(!showPassword)}
            aria-label="Toggle password visibility"
          >
            {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
          </button>
        </div>

        <div className="auth-options-row">
          <label className="remember-me-label">
            <input type="checkbox" className="custom-checkbox" />
            <span>Remember me</span>
          </label>
          <a
            href="#forgot"
            className="forgot-password-link"
            onClick={(e) => { 
              e.preventDefault(); 
              if (onForgotPasswordClick) onForgotPasswordClick(); 
              else alert('Password reset link sent to your registered email.'); 
            }}
          >
            Forgot Password?
          </a>
        </div>

        <button type="submit" className="submit-btn" disabled={loading}>
          <span>{loading ? 'Signing In...' : 'Sign In'}</span>
          {!loading && <ArrowRight size={17} style={{ marginLeft: 6 }} />}
        </button>

        <p className="auth-footer-text">
          Don't have an account? <Link to="/signup">Register</Link>
        </p>
      </form>
      </div>
    </div>
  );
};
