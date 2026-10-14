import React, { useState, useEffect, useRef } from 'react';
import { Link, useNavigate, useLocation } from 'react-router-dom';
import { User, LogOut, Edit3, X, Check, Building, Phone, Shield, Coins } from 'lucide-react';
import { getPersonDetails, logoutUser } from '../api/AuthApi';
import type { PersonData } from '../Dto/PersonDetailsDto';
import SilaLogo from '../assets/SILA Fatoora - Logo.png';

export const Header: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [isProfileOpen, setIsProfileOpen] = useState(false);
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // User state fetched dynamically from API
  const [user, setUser] = useState<Partial<PersonData>>({ name: 'User', email: 'user@silafatora.com', role: 'User' });

  const [editName, setEditName] = useState(user.name || '');
  const [editEmail, setEditEmail] = useState(user.email || '');
  const [editMobile, setEditMobile] = useState(user.mobile || '');
  const [editCompany, setEditCompany] = useState(user.companyName || '');
  const [editCountry, setEditCountry] = useState(user.country || '');
  const [editAddress, setEditAddress] = useState(user.address || '');

  // Only show profile icon on logged-in dashboard route
  const showProfile = location.pathname === '/dashboard';

  // Only show the "Powered by CAS" badge on the login/signup pages, not once logged in
  const showPoweredBy = location.pathname === '/login' || location.pathname === '/signup';

  useEffect(() => {
    if (showProfile) {
      getPersonDetails()
        .then((res) => {
          if ('data' in res && res.data) {
            setUser(res.data);
            setEditName(res.data.name || '');
            setEditEmail(res.data.email || '');
            setEditMobile(res.data.mobile || '');
            setEditCompany(res.data.companyName || '');
            setEditCountry(res.data.country || '');
            setEditAddress(res.data.address || '');
          }
        })
        .catch((err) => {
          console.error('Failed to fetch person details', err);
        });
    }
  }, [showProfile]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsProfileOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleLogout = async () => {
    try {
      await logoutUser();
    } catch (err) {
      console.error('Logout error:', err);
    } finally {
      setIsProfileOpen(false);
      navigate('/login');
    }
  };

  const handleSaveProfile = (e: React.FormEvent) => {
    e.preventDefault();
    const updated = {
      ...user,
      name: editName,
      email: editEmail,
      mobile: editMobile,
      companyName: editCompany,
      country: editCountry,
      address: editAddress,
    };
    setUser(updated);
    setIsEditModalOpen(false);
    setIsProfileOpen(false);
  };

  const userNameDisplay = user.name || user.userName || 'Account';
  const avatarLetter = userNameDisplay.charAt(0).toUpperCase();

  return (
    <header className="header-bar" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
      <Link to="/" className="header-brand">
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <img src={SilaLogo} alt="Sila Fatoora Logo" style={{ height: '40px', objectFit: 'contain' }} />
        </div>
        <div className="header-brand-divider" />
        <div className="header-brand-tagline">
          <span className="header-brand-tagline-title">Intelligent Invoice Automation System</span>
          <span className="header-brand-tagline-caption">
            AI OCR<span className="dot">•</span>4-Way Match<span className="dot">•</span>Workflow<span className="dot">•</span>RPA<span className="dot">•</span>e-Invoicing<span className="dot">•</span>Analytics
          </span>
        </div>
      </Link>

      <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
        {showPoweredBy && (
        <div className="header-powered-by" title="Powered by CAS">
          <span className="header-powered-by-label">Powered by</span>
          <div className="header-powered-by-divider" />
          <span className="header-powered-by-wordmark">CAS</span>
        </div>
        )}

        {showProfile && (
        <div style={{ position: 'relative' }} ref={dropdownRef}>
          <button
            onClick={() => setIsProfileOpen(!isProfileOpen)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              padding: '4px 8px',
              borderRadius: '20px',
              transition: 'background 0.2s ease'
            }}
            title="User Profile & Settings"
          >
            <div style={{
              width: '34px',
              height: '34px',
              borderRadius: '50%',
              background: 'linear-gradient(135deg, #0f68e6 0%, #0c57c8 100%)',
              color: '#ffffff',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontWeight: 700,
              fontSize: '14px',
              boxShadow: '0 2px 6px rgba(15, 104, 230, 0.3)'
            }}>
              {avatarLetter ? avatarLetter : <User size={18} />}
            </div>
            <span style={{ fontSize: '13px', fontWeight: 600, color: '#334155' }}>
              {userNameDisplay}
            </span>
          </button>

          {/* Profile Dropdown Menu */}
          {isProfileOpen && (
            <div style={{
              position: 'absolute',
              right: 0,
              top: 'calc(100% + 8px)',
              width: '260px',
              backgroundColor: '#ffffff',
              borderRadius: '14px',
              boxShadow: '0 10px 25px rgba(15, 23, 42, 0.15)',
              border: '1px solid #e2e8f0',
              padding: '12px',
              zIndex: 1000,
              animation: 'fadeIn 0.15s ease-out'
            }}>
              <div style={{ paddingBottom: '10px', borderBottom: '1px solid #f1f5f9' }}>
                <div style={{ fontSize: '15px', fontWeight: 700, color: '#0f172a' }}>
                  {userNameDisplay}
                </div>
                <div style={{ fontSize: '12px', color: '#64748b', marginTop: '2px', wordBreak: 'break-all' }}>
                  {user.email || 'user@silafatora.com'}
                </div>
                
                {/* Badges for Role & Credits */}
                <div style={{ display: 'flex', gap: '6px', marginTop: '8px', flexWrap: 'wrap' }}>
                  {user.role && (
                    <span style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '4px',
                      fontSize: '11px',
                      fontWeight: 700,
                      padding: '2px 8px',
                      borderRadius: '12px',
                      background: '#e0f2fe',
                      color: '#0369a1'
                    }}>
                      <Shield size={12} /> {user.role}
                    </span>
                  )}
                  {user.creditsRemaining !== undefined && (
                    <span style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '4px',
                      fontSize: '11px',
                      fontWeight: 700,
                      padding: '2px 8px',
                      borderRadius: '12px',
                      background: '#fef3c7',
                      color: '#b45309'
                    }}>
                      <Coins size={12} /> {user.creditsRemaining} Credits
                    </span>
                  )}
                </div>
              </div>

              {/* Quick Info */}
              {(user.companyName || user.mobile) && (
                <div style={{ padding: '8px 0', borderBottom: '1px solid #f1f5f9', fontSize: '12px', color: '#475569', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                  {user.companyName && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <Building size={13} color="#64748b" /> {user.companyName}
                    </div>
                  )}
                  {user.mobile && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <Phone size={13} color="#64748b" /> {user.mobile}
                    </div>
                  )}
                </div>
              )}

              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginTop: '8px' }}>
                <button
                  onClick={() => {
                    setIsEditModalOpen(true);
                    setIsProfileOpen(false);
                  }}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    width: '100%',
                    padding: '8px 12px',
                    background: 'none',
                    border: 'none',
                    borderRadius: '8px',
                    fontSize: '13px',
                    fontWeight: 600,
                    color: '#334155',
                    cursor: 'pointer',
                    textAlign: 'left'
                  }}
                  onMouseEnter={(e) => e.currentTarget.style.backgroundColor = '#f8fafc'}
                  onMouseLeave={(e) => e.currentTarget.style.backgroundColor = 'transparent'}
                >
                  <Edit3 size={15} color="#0f68e6" />
                  View & Edit Profile
                </button>

                <button
                  onClick={handleLogout}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    width: '100%',
                    padding: '8px 12px',
                    background: 'none',
                    border: 'none',
                    borderRadius: '8px',
                    fontSize: '13px',
                    fontWeight: 600,
                    color: '#dc2626',
                    cursor: 'pointer',
                    textAlign: 'left'
                  }}
                  onMouseEnter={(e) => e.currentTarget.style.backgroundColor = '#fef2f2'}
                  onMouseLeave={(e) => e.currentTarget.style.backgroundColor = 'transparent'}
                >
                  <LogOut size={15} color="#dc2626" />
                  Sign Out
                </button>
              </div>
            </div>
          )}
        </div>
        )}
      </div>

      {/* Edit Profile Modal */}
      {isEditModalOpen && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: 'rgba(15, 23, 42, 0.4)',
          backdropFilter: 'blur(4px)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 2000
        }}>
          <div style={{
            background: '#ffffff',
            borderRadius: '16px',
            width: '100%',
            maxWidth: '480px',
            padding: '24px',
            boxShadow: '0 20px 40px rgba(0, 0, 0, 0.2)',
            border: '1px solid #e2e8f0',
            maxHeight: '90vh',
            overflowY: 'auto'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '18px' }}>
              <div>
                <h3 style={{ fontSize: '18px', fontWeight: 700, color: '#0f172a' }}>User Profile Details</h3>
                <p style={{ fontSize: '12px', color: '#64748b', marginTop: '2px' }}>Live account details from Sila Fatoora</p>
              </div>
              <button
                onClick={() => setIsEditModalOpen(false)}
                style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer' }}
              >
                <X size={18} />
              </button>
            </div>

            <form onSubmit={handleSaveProfile} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                <div>
                  <label style={{ fontSize: '12px', fontWeight: 600, color: '#475569', display: 'block', marginBottom: '4px' }}>Full Name</label>
                  <input
                    type="text"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    style={{ width: '100%', height: '36px', padding: '0 12px', borderRadius: '8px', border: '1px solid #cbd5e1', fontSize: '13px', outline: 'none' }}
                    required
                  />
                </div>

                <div>
                  <label style={{ fontSize: '12px', fontWeight: 600, color: '#475569', display: 'block', marginBottom: '4px' }}>Username</label>
                  <input
                    type="text"
                    value={user.userName || ''}
                    disabled
                    style={{ width: '100%', height: '36px', padding: '0 12px', borderRadius: '8px', border: '1px solid #e2e8f0', background: '#f8fafc', fontSize: '13px', color: '#64748b' }}
                  />
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                <div>
                  <label style={{ fontSize: '12px', fontWeight: 600, color: '#475569', display: 'block', marginBottom: '4px' }}>Email Address</label>
                  <input
                    type="email"
                    value={editEmail}
                    onChange={(e) => setEditEmail(e.target.value)}
                    style={{ width: '100%', height: '36px', padding: '0 12px', borderRadius: '8px', border: '1px solid #cbd5e1', fontSize: '13px', outline: 'none' }}
                    required
                  />
                </div>

                <div>
                  <label style={{ fontSize: '12px', fontWeight: 600, color: '#475569', display: 'block', marginBottom: '4px' }}>Role</label>
                  <input
                    type="text"
                    value={user.role || 'User'}
                    disabled
                    style={{ width: '100%', height: '36px', padding: '0 12px', borderRadius: '8px', border: '1px solid #e2e8f0', background: '#f8fafc', fontSize: '13px', color: '#64748b' }}
                  />
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                <div>
                  <label style={{ fontSize: '12px', fontWeight: 600, color: '#475569', display: 'block', marginBottom: '4px' }}>Company Name</label>
                  <input
                    type="text"
                    value={editCompany}
                    onChange={(e) => setEditCompany(e.target.value)}
                    style={{ width: '100%', height: '36px', padding: '0 12px', borderRadius: '8px', border: '1px solid #cbd5e1', fontSize: '13px', outline: 'none' }}
                  />
                </div>

                <div>
                  <label style={{ fontSize: '12px', fontWeight: 600, color: '#475569', display: 'block', marginBottom: '4px' }}>Mobile Number</label>
                  <input
                    type="text"
                    value={editMobile}
                    onChange={(e) => setEditMobile(e.target.value)}
                    style={{ width: '100%', height: '36px', padding: '0 12px', borderRadius: '8px', border: '1px solid #cbd5e1', fontSize: '13px', outline: 'none' }}
                  />
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                <div>
                  <label style={{ fontSize: '12px', fontWeight: 600, color: '#475569', display: 'block', marginBottom: '4px' }}>Country</label>
                  <input
                    type="text"
                    value={editCountry}
                    onChange={(e) => setEditCountry(e.target.value)}
                    style={{ width: '100%', height: '36px', padding: '0 12px', borderRadius: '8px', border: '1px solid #cbd5e1', fontSize: '13px', outline: 'none' }}
                  />
                </div>

                <div>
                  <label style={{ fontSize: '12px', fontWeight: 600, color: '#475569', display: 'block', marginBottom: '4px' }}>Credits Remaining</label>
                  <input
                    type="text"
                    value={user.creditsRemaining !== undefined ? `${user.creditsRemaining} Credits` : '3 Credits'}
                    disabled
                    style={{ width: '100%', height: '36px', padding: '0 12px', borderRadius: '8px', border: '1px solid #e2e8f0', background: '#f8fafc', fontSize: '13px', color: '#b45309', fontWeight: 700 }}
                  />
                </div>
              </div>

              <div>
                <label style={{ fontSize: '12px', fontWeight: 600, color: '#475569', display: 'block', marginBottom: '4px' }}>Address</label>
                <input
                  type="text"
                  value={editAddress}
                  onChange={(e) => setEditAddress(e.target.value)}
                  style={{ width: '100%', height: '36px', padding: '0 12px', borderRadius: '8px', border: '1px solid #cbd5e1', fontSize: '13px', outline: 'none' }}
                />
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '12px' }}>
                <button
                  type="button"
                  onClick={() => setIsEditModalOpen(false)}
                  style={{ padding: '8px 16px', borderRadius: '8px', border: '1px solid #cbd5e1', background: '#ffffff', color: '#475569', fontSize: '13px', fontWeight: 600, cursor: 'pointer' }}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  style={{ padding: '8px 18px', borderRadius: '8px', border: 'none', background: '#0f68e6', color: '#ffffff', fontSize: '13px', fontWeight: 700, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '4px' }}
                >
                  <Check size={16} /> Save Changes
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </header>
  );
};
