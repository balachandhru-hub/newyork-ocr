import React, { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { LoginPage } from './pages/LoginPage';
import { SignupPage } from './pages/SignupPage';
import { DashboardPage } from './pages/DashboardPage';
import InvoiceExtractor from './pages/InvoiceExtractor';
import { UaeModule } from './uae/UaeModule';
import { refreshToken } from './api/AuthApi';
import { ToastContainer } from 'react-toastify';
import 'react-toastify/dist/ReactToastify.css';

export const App: React.FC = () => {
  useEffect(() => {
    // Proactive background token refresh every 18 minutes (1,080,000 ms) to prevent session expiration
    const EIGHTEEN_MINUTES = 18 * 60 * 1000;
    const interval = setInterval(() => {
      if (!window.location.pathname.includes('/login') && !window.location.pathname.includes('/signup')) {
        refreshToken().catch((err) => {
          console.warn('Background token refresh attempt failed:', err);
        });
      }
    }, EIGHTEEN_MINUTES);

    return () => clearInterval(interval);
  }, []);

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/login" replace />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/invoice_extractor" element={<InvoiceExtractor />} />
        <Route path="/uae-einvoicing" element={<UaeModule />} />
        <Route path="/uae-einvoicing/transactions/:txnId" element={<UaeModule />} />
        <Route path="/uae-einvoicing/:section" element={<UaeModule />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
      <ToastContainer position="top-right" autoClose={5000} newestOnTop closeOnClick />
    </BrowserRouter>
  );
};

export default App;
