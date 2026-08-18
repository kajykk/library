'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Library, NotebookPen, Search, Share2, Settings } from 'lucide-react';

const LINKS = [
  { href: '/library', label: '书架', icon: Library },
  { href: '/notes', label: '笔记', icon: NotebookPen },
  { href: '/graph', label: '图谱', icon: Share2 },
  { href: '/search', label: '检索', icon: Search },
  { href: '/settings', label: '设置', icon: Settings },
];

export default function Nav() {
  const pathname = usePathname();

  return (
    <nav className="bg-slate-900 text-slate-300">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center h-10">
        <Link href="/library" className="font-semibold text-white mr-6 text-sm">
          个人知识库
        </Link>
        <div className="flex items-center gap-1">
          {LINKS.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(href + '/');
            return (
              <Link
                key={href}
                href={href}
                className={`flex items-center gap-1.5 px-3 py-1 rounded text-sm transition-colors ${
                  active ? 'bg-slate-700 text-white' : 'hover:bg-slate-800 hover:text-white'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {label}
              </Link>
            );
          })}
        </div>
      </div>
    </nav>
  );
}
