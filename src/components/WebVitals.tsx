'use client';

/**
 * Web Vitals 上报（Next.js 内置指标）
 *
 * 通过 next 的 useReportWebVitals 采集 LCP / FCP / CLS / INP / TTFB，
 * 生产环境输出到 console，可替换为自建埋点 / Sentry。
 *
 * 用法：<WebVitals /> 放入根 layout
 */
import { useReportWebVitals } from 'next/web-vitals';

type MetricName =
  | 'TTFB'
  | 'FCP'
  | 'LCP'
  | 'CLS'
  | 'INP'
  | 'Next.js-hydration'
  | 'Next.js-route-change-to-render'
  | 'Next.js-render';

function ratingFor(name: string, value: number): 'good' | 'needs-improvement' | 'poor' {
  if (name === 'CLS') {
    if (value <= 0.1) return 'good';
    if (value <= 0.25) return 'needs-improvement';
    return 'poor';
  }
  const good = name === 'LCP' ? 2500 : name === 'INP' ? 200 : name === 'TTFB' ? 800 : 1800;
  const poor = name === 'LCP' ? 4000 : name === 'INP' ? 500 : name === 'TTFB' ? 1800 : 3000;
  if (value <= good) return 'good';
  if (value <= poor) return 'needs-improvement';
  return 'poor';
}

export default function WebVitals() {
  useReportWebVitals((metric) => {
    // 生产环境输出；接入埋点时替换此分支
    if (process.env.NODE_ENV === 'production') {
      const rating = ratingFor(metric.name as MetricName, metric.value);
      console.info(`[WebVitals] ${metric.name}: ${metric.value.toFixed(1)} (${rating})`);
    }
  });
  return null;
}
