export interface DocMeta {
  workflow?: 'inbox' | 'review' | 'done';
  excerpt?: string;
  totalPages?: number;
  [key: string]: unknown;
}
