import React, { useState } from 'react';
import { Mail, ArrowRight, ShieldCheck, Key, Lock, Eye, EyeOff } from 'lucide-react';
import { forgotPassword, resetPassword } from '../api/AuthApi';
import { toast } from 'react-toastify';

export const ForgotPasswordForm: React.FC = () => {
  const [step, setStep] = useState<'forgot' | 'reset'>('forgot');
  const [email, setEmail] = useState('');
  const [resetCode, setResetCode] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const handleForgotSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email) {
      setMessage({ type: 'error', text: 'Please enter your email address.' });
      return;
    }

    setLoading(true);
    setMessage(null);

    try {
      const res = await forgotPassword(email);
      if (res && res.error) {
        setMessage({ type: 'error', text: res.message || 'Failed to send reset link.' });
      } else {
        setMessage({ type: 'success', text: 'Password reset code sent to your email.' });
        toast.success('Reset code sent!');
        setStep('reset');
      }
    } catch (err: any) {
      setMessage({ type: 'error', text: 'An unexpected error occurred.' });
    } finally {
      setLoading(false);
    }
  };

  const handleResetSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!resetCode || !newPassword || !confirmPassword) {
      setMessage({ type: 'error', text: 'Please fill in all fields.' });
      return;
    }
    if (newPassword !== confirmPassword) {
      setMessage({ type: 'error', text: 'Passwords do not match.' });
      return;
    }

    setLoading(true);
    setMessage(null);

    try {
      const payload = {
        email,
        resetCode,
        newPassword,
        confirmPassword
      };
      const res = await resetPassword(payload);
      if (res && res.error) {
        setMessage({ type: 'error', text: res.message || 'Failed to reset password.' });
      } else {
        setMessage({ type: 'success', text: 'Password reset successfully! You can now log in.' });
        toast.success('Password reset successfully!');
      }
    } catch (err: any) {
      setMessage({ type: 'error', text: 'An unexpected error occurred.' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-card" style={{ maxWidth: '400px', width: '100%', margin: '0 auto', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
      <div className="auth-card-header">
        <div className="auth-brand-pill" style={{ backgroundColor: '#f0fdf4', color: '#16a34a', border: '1px solid #bbf7d0' }}>
          <ShieldCheck size={14} color="#16a34a" />
          <span>Password Recovery</span>
        </div>
        <h1 className="auth-card-title">{step === 'forgot' ? 'Forgot Password' : 'Reset Password'}</h1>
        <p className="auth-card-subtitle">
          {step === 'forgot' 
            ? 'Enter your registered email to reset your password' 
            : 'Enter the reset code sent to your email and your new password'}
        </p>
      </div>

      {message && (
        <div className={`auth-error-box ${message.type === 'success' ? 'success' : ''}`} style={message.type === 'success' ? { backgroundColor: '#f0fdf4', color: '#166534', border: '1px solid #bbf7d0', padding: '12px', borderRadius: '8px', marginBottom: '16px', fontSize: '13px' } : {}}>
          {message.text}
        </div>
      )}

      {step === 'forgot' ? (
        <form className="auth-form" onSubmit={handleForgotSubmit}>
          <div className="input-field-group icon-input-wrapper">
            <Mail size={17} className="input-icon" />
            <input
              type="email"
              className="custom-input icon-padded"
              placeholder="Email Address"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>

          <button type="submit" className="submit-btn" disabled={loading} style={{ marginTop: '10px' }}>
            <span>{loading ? 'Sending Code...' : 'Send Reset Code'}</span>
            {!loading && <ArrowRight size={17} style={{ marginLeft: 6 }} />}
          </button>
        </form>
      ) : (
        <form className="auth-form" onSubmit={handleResetSubmit}>
          <div className="input-field-group icon-input-wrapper" style={{ marginBottom: '12px' }}>
            <Mail size={17} className="input-icon" />
            <input
              type="email"
              className="custom-input icon-padded"
              value={email}
              readOnly
              style={{ backgroundColor: '#f1f5f9', cursor: 'not-allowed' }}
            />
          </div>

          <div className="input-field-group icon-input-wrapper" style={{ marginBottom: '12px' }}>
            <Key size={17} className="input-icon" />
            <input
              type="text"
              className="custom-input icon-padded"
              placeholder="Reset Code"
              value={resetCode}
              onChange={(e) => setResetCode(e.target.value)}
              required
            />
          </div>

          <div className="input-field-group password-input-wrapper icon-input-wrapper" style={{ marginBottom: '12px' }}>
            <Lock size={17} className="input-icon" />
            <input
              type={showPassword ? 'text' : 'password'}
              className="custom-input icon-padded"
              placeholder="New Password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
            />
            <button
              type="button"
              className="password-toggle-btn"
              onClick={() => setShowPassword(!showPassword)}
            >
              {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
            </button>
          </div>

          <div className="input-field-group password-input-wrapper icon-input-wrapper">
            <Lock size={17} className="input-icon" />
            <input
              type={showPassword ? 'text' : 'password'}
              className="custom-input icon-padded"
              placeholder="Confirm Password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
            />
          </div>

          <button type="submit" className="submit-btn" disabled={loading} style={{ marginTop: '16px' }}>
            <span>{loading ? 'Resetting...' : 'Reset Password'}</span>
            {!loading && <ArrowRight size={17} style={{ marginLeft: 6 }} />}
          </button>
        </form>
      )}
    </div>
  );
};
