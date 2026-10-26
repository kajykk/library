'use client';

import Link from 'next/link';
import { Rocket, X } from 'lucide-react';

interface OnboardingBannerProps {
  onDismiss: () => void;
}

/**
 * 首次配置引导横幅（B1-08）。
 * 显示条件：非 API 模式 + 书架为空 + 无本地迁移提示 + 未点过「不再提示」。
 */
export default function OnboardingBanner({ onDismiss }: OnboardingBannerProps) {
  return (
    <div className="bg-gradient-to-r from-primary-50 to-blue-50 border border-primary-200 rounded-xl p-6 mb-6">
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-primary-100 flex items-center justify-center">
            <Rocket className="w-5 h-5 text-primary-600" />
          </div>
          <div>
            <h2 className="font-semibold text-gray-900">开始使用你的知识库</h2>
            <p className="text-sm text-gray-600">三步连接后端，解锁全部功能</p>
          </div>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="p-1.5 rounded hover:bg-gray-200 transition-colors text-gray-400"
          aria-label="不再提示"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <ol className="space-y-3 mb-5">
        <li className="flex items-start gap-3">
          <span className="w-6 h-6 rounded-full bg-primary-600 text-white flex items-center justify-center text-xs font-medium shrink-0 mt-0.5">1</span>
          <div>
            <p className="text-sm text-gray-800">启动后端服务</p>
            <code className="text-xs bg-gray-100 px-2 py-1 rounded mt-1 inline-block font-mono text-gray-600">
              cd server && uvicorn app.main:app --port 8000
            </code>
          </div>
        </li>
        <li className="flex items-start gap-3">
          <span className="w-6 h-6 rounded-full bg-primary-600 text-white flex items-center justify-center text-xs font-medium shrink-0 mt-0.5">2</span>
          <p className="text-sm text-gray-800">打开设置，填入 API 地址与 Token</p>
        </li>
        <li className="flex items-start gap-3">
          <span className="w-6 h-6 rounded-full bg-primary-600 text-white flex items-center justify-center text-xs font-medium shrink-0 mt-0.5">3</span>
          <p className="text-sm text-gray-800">点击「保存并测试」，状态条消失即成功</p>
        </li>
      </ol>

      <div className="flex items-center gap-3">
        <Link
          href="/settings"
          className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors text-sm font-medium"
        >
          去设置
        </Link>
        <button
          type="button"
          onClick={onDismiss}
          className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg transition-colors text-sm"
        >
          以后再说
        </button>
      </div>
    </div>
  );
}
