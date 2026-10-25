// CSP connect-src：构建期可覆盖，默认保持宽松（API 地址运行时配置）
const cspConnectSrc =
  process.env.NEXT_PUBLIC_CSP_CONNECT_SRC ||
  "'self' http: https: ws: wss:";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // standalone：.next/standalone 自含最小 node_modules，供 Docker runner 阶段使用，
  // 生产镜像不再携带 devDependencies（typescript/playwright/vitest 等）
  output: 'standalone',
  poweredByHeader: false,
  images: {
    // 启用 Sharp 内置优化：自适应格式（WebP/AVIF）、调整尺寸、懒加载
    // 要求宿主环境安装 sharp（已安装）
    formats: ['image/avif', 'image/webp'],
    deviceSizes: [640, 750, 828, 1080, 1200, 1920],
    imageSizes: [16, 32, 48, 64, 96, 128, 256, 384],
  },
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          {
            key: 'Content-Security-Policy',
            value: [
              "default-src 'self'",
              "script-src 'self' 'unsafe-inline'",
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data: blob: https:",
              "font-src 'self' data:",
              `connect-src ${cspConnectSrc}`,
              "worker-src 'self' blob:",
              "media-src 'self' blob: data:",
              "frame-src 'self' blob:",
              "object-src 'none'",
              "base-uri 'self'",
              "form-action 'self'",
              "frame-ancestors 'none'",
            ].join('; '),
          },
        ],
      },
    ];
  },
};

// @next/bundle-analyzer：按需启用（ANALYZE=true npm run build），产出包体积基线报告。
// 默认关闭，避免常规 dev/build 增加额外开销。
let config = nextConfig;
if (process.env.ANALYZE === 'true') {
  const withBundleAnalyzer = (await import('@next/bundle-analyzer')).default({ enabled: true });
  config = withBundleAnalyzer(nextConfig);
}

export default config;
