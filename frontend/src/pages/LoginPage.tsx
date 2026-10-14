import React, { useState } from 'react';
import { Header } from '../components/Header';
import { LoginForm } from '../components/LoginForm';
import { MonitorPreview } from '../components/MonitorPreview';
import { ForgotPasswordForm } from '../components/ForgotPasswordForm';

export const LoginPage: React.FC = () => {
  const [showForgotPassword, setShowForgotPassword] = useState(false);

  return (
    <>
      <Header />
      <main className="auth-page-container">
        <div className="auth-wrapper">
          <LoginForm onForgotPasswordClick={() => setShowForgotPassword(true)} />
          {showForgotPassword ? <ForgotPasswordForm /> : <MonitorPreview />}
        </div>
      </main>
    </>
  );
};
