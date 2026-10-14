import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { Eye, EyeOff } from 'lucide-react';
import { registerUser } from '../api/AuthApi';
import type { RegisterRequestDto } from '../Dto/RegisterDto';

const DISALLOWED_EMAIL_DOMAINS = [
  'gmail.com',
  'yahoo.com',
  'ymail.com',
  'hotmail.com',
  'outlook.com',
  'live.com',
  'icloud.com',
  'me.com',
  'aol.com',
  'proton.me',
  'protonmail.com',
  'gmx.com',
  'mail.com',
  'yandex.com',
  'rediffmail.com',
  'zoho.com',
];

const isPublicEmailDomain = (email: string): boolean => {
  const domain = email.split('@')[1]?.toLowerCase().trim();
  if (!domain) return false;
  return DISALLOWED_EMAIL_DOMAINS.includes(domain);
};

export const SignupForm: React.FC = () => {
  const [formData, setFormData] = useState({
    fullName: '',
    email: '',
    companyName: '',
    mobileNumber: '',
    country: '',
    address: '',
    username: '',
    password: '',
    confirmPassword: ''
  });

  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const navigate = useNavigate();

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.username || !formData.password || !formData.email || !formData.fullName) {
      setError('Please fill in all mandatory fields (Full Name, Email, Username, Password).');
      return;
    }

    if (isPublicEmailDomain(formData.email)) {
      setError('Registration using public email domains (Gmail, Yahoo, Hotmail, Outlook, etc.) is not allowed. Please use your official corporate or work email address.');
      return;
    }

    if (formData.password !== formData.confirmPassword) {
      setError('Password and Confirm Password do not match.');
      return;
    }

    setLoading(true);
    setError('');

    const payload: RegisterRequestDto = {
      name: formData.fullName,
      email: formData.email,
      companyName: formData.companyName || '',
      mobile: formData.mobileNumber || '',
      country: formData.country || '',
      address: formData.address || '',
      role: 'User',
      userName: formData.username,
      password: formData.password
    };

    try {
      const res = await registerUser(payload);
      if ('personId' in res && (res.personId || res.status === 200)) {
        navigate('/login');
      } else {
        setError(res.message || 'Registration failed. Please try again.');
      }
    } catch (err: any) {
      setError(err.message || 'Unable to connect to registration server.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-card signup-card">
      <h1 className="auth-card-title">Create Account</h1>
      <p className="auth-card-subtitle">Register as a Sila Fatoora client</p>

      {error && (
        <div style={{ background: '#fef2f2', color: '#dc2626', padding: '10px 16px', borderRadius: '12px', fontSize: '14px', marginBottom: '16px' }}>
          {error}
        </div>
      )}

      <form className="auth-form" onSubmit={handleSubmit}>
        <div className="auth-grid">
          {/* Row 1 */}
          <div className="input-field-group">
            <input
              type="text"
              name="fullName"
              className="custom-input"
              placeholder="Full Name"
              value={formData.fullName}
              onChange={handleChange}
              required
            />
          </div>

          <div className="input-field-group">
            <input
              type="email"
              name="email"
              className="custom-input"
              placeholder="Email Address"
              value={formData.email}
              onChange={handleChange}
              required
            />
          </div>

          {/* Row 2 */}
          <div className="input-field-group">
            <input
              type="text"
              name="companyName"
              className="custom-input"
              placeholder="Company Name"
              value={formData.companyName}
              onChange={handleChange}
            />
          </div>

          <div className="input-field-group">
            <input
              type="text"
              name="mobileNumber"
              className="custom-input"
              placeholder="Mobile Number"
              value={formData.mobileNumber}
              onChange={handleChange}
            />
          </div>

          {/* Row 3 */}
          <div className="input-field-group">
            <input
              type="text"
              name="country"
              className="custom-input"
              placeholder="Country"
              value={formData.country}
              onChange={handleChange}
            />
          </div>

          <div className="input-field-group">
            <input
              type="text"
              name="address"
              className="custom-input"
              placeholder="Address"
              value={formData.address}
              onChange={handleChange}
            />
          </div>

          {/* Row 4 */}
          <div className="input-field-group">
            <input
              type="text"
              name="username"
              className="custom-input"
              placeholder="Username"
              value={formData.username}
              onChange={handleChange}
              required
            />
          </div>

          <div className="input-field-group password-input-wrapper">
            <input
              type={showPassword ? 'text' : 'password'}
              name="password"
              className="custom-input"
              placeholder="Password"
              value={formData.password}
              onChange={handleChange}
              required
            />
            <button
              type="button"
              className="password-toggle-btn"
              onClick={() => setShowPassword(!showPassword)}
              aria-label="Toggle password visibility"
            >
              {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
            </button>
          </div>

          {/* Row 5 */}
          <div className="input-field-group password-input-wrapper input-field-full">
            <input
              type={showConfirmPassword ? 'text' : 'password'}
              name="confirmPassword"
              className="custom-input"
              placeholder="Confirm Password"
              value={formData.confirmPassword}
              onChange={handleChange}
              required
            />
            <button
              type="button"
              className="password-toggle-btn"
              onClick={() => setShowConfirmPassword(!showConfirmPassword)}
              aria-label="Toggle confirm password visibility"
            >
              {showConfirmPassword ? <EyeOff size={18} /> : <Eye size={18} />}
            </button>
          </div>
        </div>

        <button type="submit" className="submit-btn" disabled={loading} style={{ marginTop: '8px' }}>
          {loading ? 'Creating Account...' : 'Sign Up'}
        </button>

        <p className="auth-footer-text">
          Already have an account? <Link to="/login">Sign In</Link>
        </p>
      </form>
    </div>
  );
};
