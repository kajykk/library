/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // standalone：.next/standalone 自含最小 node_modules，供 Docker runner 阶段使用，
  // 生产镜像不再携带 devDependencies（typescript/playwright/vitest 等）
  output: 'standalone',
  poweredByHeader: false,
  images: {
    unoptimized: true,
  },
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
        ],
      },
    ];
  },
};

export default nextConfig;
