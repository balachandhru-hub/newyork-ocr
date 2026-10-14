import React from 'react';
import { Header } from '../components/Header';
import { DashboardView } from '../components/DashboardView';

export const DashboardPage: React.FC = () => {
  return (
    <>
      <Header />
      <DashboardView />
    </>
  );
};
