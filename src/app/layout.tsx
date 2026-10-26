import type { Metadata, Viewport } from 'next';
import './globals.css';
import Nav from '@/components/Nav';
import DataSourceStatusBar from '@/components/DataSourceStatusBar';
import ServiceWorkerRegister from '@/components/ServiceWorkerRegister';
import ErrorBoundary from '@/components/ErrorBoundary';
import LazyCommandPalette from '@/components/LazyCommandPalette';
import WebVitals from '@/components/WebVitals';
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
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        {/* 主题预置：首屏渲染前按 localStorage 恢复暗色，避免白屏闪烁（FOUC） */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{if(localStorage.getItem('kb_theme')==='dark'){document.documentElement.classList.add('dark')}}catch(e){}})()`,
          }}
        />
      </head>
      <body className="antialiased">
        <WebVitals />
        <ServiceWorkerRegister />
        <Providers>
          {/* Nav 置于错误边界之外：页面崩溃时导航仍然可用，用户不会被错误页困住 */}
          <Nav />
          <DataSourceStatusBar />
          <ErrorBoundary>
            {children}
          </ErrorBoundary>
          <LazyCommandPalette />
        </Providers>
      </body>
    </html>
  );
}
