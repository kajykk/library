'use client';

import Image from 'next/image';
import { BookOpen } from 'lucide-react';
import { getBookCoverUrl } from '@/lib/dataSource';
import { useCoverUrl } from '@/lib/coverCache';
import type { Book } from '@/lib/db';

export default function BookCover({
  book,
  variant,
}: {
  book: Pick<Book, 'id' | 'title' | 'coverPath'>;
  variant: 'grid' | 'list';
}) {
  const url = useCoverUrl(book.id, Boolean(book.coverPath), getBookCoverUrl);

  if (!url) {
    return (
      <div className="w-full h-full flex items-center justify-center">
        <BookOpen className={variant === 'grid' ? 'w-12 h-12 text-gray-300' : 'w-6 h-6 text-gray-300'} />
      </div>
    );
  }

  if (variant === 'list') {
    return (
      <Image
        src={url}
        alt={book.title}
        width={64}
        height={80}
        unoptimized
        className="w-full h-full object-cover"
      />
    );
  }

  return (
    <Image
      src={url}
      alt={book.title}
      fill
      unoptimized
      sizes="(max-width: 768px) 50vw, (max-width: 1024px) 33vw, 20vw"
      className="w-full h-full object-cover"
    />
  );
}
