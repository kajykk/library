import type { Metadata, Viewport } from 'next';
import './globals.css';
import Nav from '@/components/Nav';
import ServiceWorkerRegister from '@/components/ServiceWorkerRegister';
import ErrorBoundary from '@/components/ErrorBoundary';
import CommandPalette from '@/components/CommandPalette';
import Providers from './Providers';

export const metadata: Metadata = {
  title: '个人知识库',
  description: '自托管、本地优先的个人知识库',
  manifest: '/manifest.webmanifest',
  robots: {
    index: false,
    follow: false,
  },
};

export const viewport: Viewport = {
  themeColor: '#1e293b',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">
        <ServiceWorkerRegister />
        <Providers>
          {/* Nav 置于错误边界之外：页面崩溃时导航仍然可用，用户不会被错误页困住 */}
          <Nav />
          <ErrorBoundary>
            {children}
          </ErrorBoundary>
          <CommandPalette />
        </Providers>
      </body>
    </html>
  );
}
