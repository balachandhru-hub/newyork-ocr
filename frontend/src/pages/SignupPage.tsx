import React from 'react';
import { Header } from '../components/Header';
import { SignupForm } from '../components/SignupForm';
import { MonitorPreview } from '../components/MonitorPreview';

export const SignupPage: React.FC = () => {
  return (
    <>
      <Header />
      <main className="auth-page-container">
        <div className="auth-wrapper signup-wrapper">
          <SignupForm />
          <MonitorPreview />
        </div>
      </main>
    </>
  );
};
