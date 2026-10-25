# ---- 依赖安装阶段：仅 package*.json 参与缓存层 ----
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ---- 构建阶段：NEXT_PUBLIC_* 在此内联进产物（运行时注入无效）----
# 安全约束：只有 NEXT_PUBLIC_API_URL 允许走构建期变量。
# NEXT_PUBLIC_* 会被内联进客户端 JS bundle，任何以它命名的密钥都等于公开发布；
# API Token 必须走运行时路径（设置页写入 localStorage，见 src/lib/api/client.ts）。
FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---- 运行阶段：standalone 产物 + 非 root 用户，不含 devDependencies/源码/构建工具 ----
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup -S nodejs -g 1001 && adduser -S nextjs -u 1001

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

# 直接 exec node：信号（SIGTERM）直达进程，优雅退出优于 npm 包一层
CMD ["node", "server.js"]
