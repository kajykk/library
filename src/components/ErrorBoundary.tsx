'use client';

import { Component, ReactNode } from 'react';

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  override componentDidCatch(error: Error) {
    console.error('Application error boundary caught an error:', error);
  }

  render() {
    if (this.state.error) {
      return (
        <main className="min-h-[60vh] flex items-center justify-center px-4">
          <div className="max-w-lg w-full rounded-2xl border border-red-200 bg-red-50 p-8 text-center shadow-sm">
            <h1 className="text-xl font-semibold text-red-800">页面加载出错</h1>
            <p className="mt-3 text-sm leading-6 text-red-700">
              组件运行时发生异常。请刷新页面重试，或返回书架后重新打开。
            </p>
            <button
              type="button"
              onClick={() => this.setState({ error: null })}
              className="mt-6 inline-flex items-center justify-center rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-700"
            >
              重试
            </button>
          </div>
        </main>
      );
    }

    return this.props.children;
  }
}
