'use client';

/** 斜杠命令菜单：命令表 + 浮层渲染（从 NoteEditor 抽离） */

import type { Editor } from '@tiptap/react';
import type { ReactNode } from 'react';
import {
  Heading1,
  Heading2,
  Heading3,
  List,
  ListOrdered,
  Quote,
  Code,
  Minus,
  Link2,
} from 'lucide-react';

export interface SlashItem {
  id: string;
  label: string;
  hint: string;
  icon: ReactNode;
  apply: (editor: Editor) => void;
}

export const SLASH_ITEMS: SlashItem[] = [
  { id: 'h1', label: '一级标题', hint: 'H1', icon: <Heading1 className="w-4 h-4" />, apply: (e) => e.chain().focus().toggleHeading({ level: 1 }).run() },
  { id: 'h2', label: '二级标题', hint: 'H2', icon: <Heading2 className="w-4 h-4" />, apply: (e) => e.chain().focus().toggleHeading({ level: 2 }).run() },
  { id: 'h3', label: '三级标题', hint: 'H3', icon: <Heading3 className="w-4 h-4" />, apply: (e) => e.chain().focus().toggleHeading({ level: 3 }).run() },
  { id: 'bullet', label: '无序列表', hint: '•', icon: <List className="w-4 h-4" />, apply: (e) => e.chain().focus().toggleBulletList().run() },
  { id: 'ordered', label: '有序列表', hint: '1.', icon: <ListOrdered className="w-4 h-4" />, apply: (e) => e.chain().focus().toggleOrderedList().run() },
  { id: 'quote', label: '引用', hint: '❝', icon: <Quote className="w-4 h-4" />, apply: (e) => e.chain().focus().toggleBlockquote().run() },
  { id: 'code', label: '代码块', hint: '</>', icon: <Code className="w-4 h-4" />, apply: (e) => e.chain().focus().toggleCodeBlock().run() },
  { id: 'rule', label: '分隔线', hint: '——', icon: <Minus className="w-4 h-4" />, apply: (e) => e.chain().focus().setHorizontalRule().run() },
  { id: 'wikilink', label: '双链链接', hint: '[[', icon: <Link2 className="w-4 h-4" />, apply: (e) => e.chain().focus().insertContent('[[').run() },
];

export interface SlashMenuState {
  query: string;
  top: number;
  left: number;
  index: number;
}

interface SlashMenuProps {
  state: SlashMenuState;
  items: SlashItem[];
  onHover: (index: number) => void;
  onApply: (index?: number) => void;
}

export default function SlashMenu({ state, items, onHover, onApply }: SlashMenuProps) {
  if (items.length === 0) return null;
  return (
    <div
      className="absolute z-30 w-72 rounded-lg border border-gray-200 bg-white shadow-lg overflow-hidden"
      style={{ top: state.top + 24, left: state.left }}
    >
      {items.map((item, i) => (
        <button
          key={item.id}
          onMouseEnter={() => onHover(i)}
          onClick={() => onApply(i)}
          className={`flex w-full items-center gap-2.5 text-left px-3 py-2 text-sm transition-colors ${
            i === state.index ? 'bg-primary-50 text-primary-700' : 'text-gray-700 hover:bg-gray-50'
          }`}
        >
          <span className="text-gray-400">{item.icon}</span>
          <span className="font-medium">{item.label}</span>
          <span className="ml-auto text-xs text-gray-400">{item.hint}</span>
        </button>
      ))}
    </div>
  );
}
