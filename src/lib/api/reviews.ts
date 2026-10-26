/** 间隔复习 API（契约第 7 节）。 */

import { request } from './client';

export type ReviewRating = 'again' | 'hard' | 'good' | 'easy';

export interface ReviewCard {
  annotation_id: string;
  document_id: string;
  document_title: string;
  document_type: string;
  type: string;
  anchor: Record<string, unknown>;
  quote: string;
  content: string;
  created_at: string | null;
  due_at: string | null;
  interval_days: number;
  repetitions: number;
  ease: number;
  /** 后端算好的四档间隔文案，前端不自行计算 */
  preview: Record<ReviewRating, string>;
}

export interface DueReviews {
  total: number;
  cards: ReviewCard[];
}

export interface ReviewSchedule {
  annotation_id: string;
  due_at: string | null;
  interval_days: number;
  ease: number;
  repetitions: number;
  lapses: number;
  next_preview: Record<ReviewRating, string>;
}

export interface ReviewStats {
  tz_offset_minutes: number;
  due_now: number;
  due_today: number;
  new_today: number;
  reviewed_today: number;
  upcoming_7d: number;
}

export function getDueReviews(
  params: { limit?: number; newLimit?: number; documentId?: string } = {},
): Promise<DueReviews> {
  const qs: Record<string, string> = {};
  if (params.limit != null) qs.limit = String(params.limit);
  if (params.newLimit != null) qs.new_limit = String(params.newLimit);
  if (params.documentId) qs.document_id = params.documentId;
  const query = new URLSearchParams(qs).toString();
  return request<DueReviews>(`/api/reviews/due${query ? `?${query}` : ''}`);
}

export function rateReview(annotationId: string, rating: ReviewRating): Promise<ReviewSchedule> {
  return request<ReviewSchedule>(`/api/reviews/${annotationId}/rate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rating }),
  });
}

export function snoozeReview(annotationId: string, days: 1 | 3 | 7 = 1): Promise<ReviewSchedule> {
  return request<ReviewSchedule>(`/api/reviews/${annotationId}/snooze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ days }),
  });
}

/** tzOffsetMinutes = -new Date().getTimezoneOffset()（东八区为 480）。 */
export function getReviewStats(tzOffsetMinutes: number): Promise<ReviewStats> {
  const qs = new URLSearchParams({ tz_offset_minutes: String(tzOffsetMinutes) });
  return request<ReviewStats>(`/api/reviews/stats?${qs}`);
}
