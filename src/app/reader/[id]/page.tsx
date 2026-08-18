'use client';

import dynamic from 'next/dynamic';
import { Suspense, useEffect, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { Book } from '@/lib/db';
import { getBookById } from '@/lib/dataSource';
import { BookOpen, ArrowLeft } from 'lucide-react';
import ErrorBoundary from '@/components/ErrorBoundary';

const BookReader = dynamic(() => import('@/components/BookReader'), {
  ssr: false,
  loading: () => (
    <div className="flex h-screen items-center justify-center text-gray-400">
      <div className="text-center">
        <BookOpen className="mx-auto mb-3 h-10 w-10 animate-pulse text-gray-300" />
        正在打开阅读器…
      </div>
    </div>
  ),
});

export default function ReaderPage() {
  return (
    <Suspense fallback={<div className="p-16 text-center text-gray-500">加载中...</div>}>
      <ReaderPageInner />
    </Suspense>
  );
}

function ReaderPageInner() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const positionParam = searchParams.get('position');
  const overridePosition = positionParam !== null && !Number.isNaN(Number(positionParam))
    ? Number(positionParam)
    : undefined;
  const [book, setBook] = useState<Book | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getBookById(params.id)
      .then((b) => {
        if (b) {
          setBook(b);
        } else {
          setError('未找到该文档：可能已被删除，或后端不可用时本地库中没有此书');
        }
      })
      .catch((e) => setError((e as Error).message));
  }, [params.id]);

  if (error) {
    return (
      <main className="max-w-3xl mx-auto px-4 py-16 text-center">
        <BookOpen className="w-12 h-12 text-gray-300 mx-auto mb-4" />
        <p className="text-gray-600">{error}</p>
        <Link
          href="/library"
          className="inline-flex items-center gap-2 mt-6 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          返回书架
        </Link>
      </main>
    );
  }

  if (!book) {
    return <div className="p-16 text-center text-gray-500">加载中...</div>;
  }

  return (
    <ErrorBoundary>
      <BookReader
        book={book}
        onClose={() => router.push('/library')}
        initialPositionOverride={overridePosition}
      />
    </ErrorBoundary>
  );
}
