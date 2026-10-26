'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { History, Library, NotebookPen, Search, Settings, Share2, ShieldCheck, Upload } from 'lucide-react';
import ThemeToggle from '@/components/ThemeToggle';

const LINKS = [
  { href: '/library', label: '书架', icon: Library },
  { href: '/import', label: '导入', icon: Upload },
  { href: '/review', label: '回顾', icon: History },
  { href: '/notes', label: '笔记', icon: NotebookPen },
  { href: '/graph', label: '图谱', icon: Share2 },
  { href: '/search', label: '检索', icon: Search },
  { href: '/quality', label: '质量', icon: ShieldCheck },
  { href: '/settings', label: '设置', icon: Settings },
];

const KB_SIGNAL = [
  '本地优先',
  '双链整理',
  '全文检索',
  '实时协作',
];

export default function Nav() {
  const pathname = usePathname();

  return (
    <nav className="bg-slate-900 text-slate-300">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between h-10 gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <Link href="/library" className="font-semibold text-white text-sm whitespace-nowrap">
            <span className="hidden sm:inline">个人知识库</span>
            <span className="sm:hidden">知识库</span>
          </Link>
          <div className="hidden md:flex items-center gap-1 text-[11px] text-slate-400">
            {KB_SIGNAL.map((item) => (
              <span key={item} className="px-2 py-0.5 rounded-full bg-slate-800/80 border border-slate-700">
                {item}
              </span>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {LINKS.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(href + '/');
            return (
              <Link
                key={href}
                href={href}
                aria-label={label}
                title={label}
                className={`flex items-center gap-1.5 px-1.5 sm:px-3 py-1 rounded text-sm transition-colors ${
                  active ? 'bg-slate-700 text-white' : 'hover:bg-slate-800 hover:text-white'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">{label}</span>
              </Link>
            );
          })}
          <ThemeToggle />
        </div>
      </div>
    </nav>
  );
}
