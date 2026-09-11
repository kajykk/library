'use client';

/**
 * 阅读器选中文字查询弹窗（对标 Readest 词典/Wikipedia 查询）：
 * - 单个英文单词 → dictionaryapi.dev 英英释义
 * - 其他（含中文）→ MyMemory 免费翻译（中英双向）
 * 纯前端调用免费免 key 端点，超时/失败显示友好错误。
 */

import { useEffect, useState } from 'react';
import { BookOpenText, Languages, Loader2, X } from 'lucide-react';

interface LookupPopoverProps {
  text: string;
  x: number;
  y: number;
  onClose: () => void;
}

interface WordSense {
  partOfSpeech: string;
  definitions: Array<{ definition: string; example?: string }>;
}

interface WordResult {
  word: string;
  phonetic: string;
  senses: WordSense[];
  source: string;
  url?: string;
}

interface TranslationResult {
  source: string;
  translated: string;
  langpair: string;
}

const IS_WORD = /^[A-Za-z][A-Za-z'’-]*$/;
const HAS_CJK = /[\u3400-\u9fff]/;

function pickLangpair(text: string): string {
  return HAS_CJK.test(text) ? 'zh|en' : 'en|zh';
}

async function lookupWord(word: string): Promise<WordResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const resp = await fetch(`https://api.dictionaryapi.dev/api/v2/entries/en/${encodeURIComponent(word)}`, {
      signal: controller.signal,
    });
    if (resp.status === 404) throw new Error('词典中没有这个词');
    if (!resp.ok) throw new Error(`词典请求失败：${resp.status}`);
    const data = await resp.json();
    const entry = Array.isArray(data) ? data[0] : data;
    if (!entry) throw new Error('词典中没有这个词');

    const senses: WordSense[] = [];
    for (const meaning of entry.meanings || []) {
      const part = meaning.partOfSpeech || '';
      for (const def of (meaning.definitions || []).slice(0, 3)) {
        const cleaned = (def.definition || '').replace(/^\(.+?\)\s*/, '');
        senses.push({ partOfSpeech: part, definitions: [{ definition: cleaned, example: def.example }] });
      }
    }
    return {
      word: entry.word || word,
      phonetic: entry.phonetic || entry.phonetics?.find((p: { text?: string }) => p.text)?.text || '',
      senses,
      source: 'dictionaryapi.dev',
      url: `https://dictionaryapi.dev/search?q=${encodeURIComponent(word)}`,
    };
  } finally {
    clearTimeout(timer);
  }
}

async function lookupTranslate(text: string, langpair: string): Promise<TranslationResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const resp = await fetch(
      `https://api.mymemory.translated.net/get?q=${encodeURIComponent(text)}&langpair=${langpair}`,
      { signal: controller.signal },
    );
    if (!resp.ok) throw new Error(`翻译请求失败：${resp.status}`);
    const data = await resp.json();
    if (data.responseStatus && Number(data.responseStatus) !== 200) {
      // MyMemory 将配额超限等错误放在 responseStatus
      throw new Error(`翻译服务：${data.responseDetails ?? data.responseStatus}`);
    }
    return {
      source: text,
      translated: data.responseData?.translatedText || '',
      langpair,
    };
  } finally {
    clearTimeout(timer);
  }
}

export default function LookupPopover({ text, x, y, onClose }: LookupPopoverProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [word, setWord] = useState<WordResult | null>(null);
  const [translation, setTranslation] = useState<TranslationResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    const trimmed = text.trim();
    const isWord = IS_WORD.test(trimmed);
    setLoading(true);
    setError(null);
    setWord(null);
    setTranslation(null);
    if (isWord) {
      lookupWord(trimmed.toLowerCase())
        .then((w) => {
          if (!cancelled) setWord(w);
        })
        .catch((e) => {
          if (!cancelled) setError((e as Error).message);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    } else {
      const langpair = pickLangpair(trimmed);
      lookupTranslate(trimmed, langpair)
        .then((t) => {
          if (!cancelled) setTranslation(t);
        })
        .catch((e) => {
          if (!cancelled) setError((e as Error).message);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  return (
    <div
      className="fixed z-[61] w-80 max-h-[360px] rounded-xl border border-gray-200 bg-white shadow-xl overflow-y-auto"
      style={{ left: x, top: y, transform: 'translate(-50%, -105%)' }}
      onMouseDown={(e) => e.preventDefault()}
      role="dialog"
      aria-label="查询结果"
    >
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <Languages className="w-3.5 h-3.5 text-primary-500 shrink-0" />
          <span className="text-sm font-medium text-gray-800 truncate">{text.trim()}</span>
        </div>
        <button
          onClick={onClose}
          className="p-1 text-gray-400 hover:text-gray-600"
          aria-label="关闭查询"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="px-3 py-2.5">
        {loading ? (
          <div className="flex items-center gap-2 py-4 text-sm text-gray-400">
            <Loader2 className="w-4 h-4 animate-spin" /> 查询中...
          </div>
        ) : error ? (
          <p className="text-sm text-gray-500 py-3">{error}</p>
        ) : word ? (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-base font-semibold text-gray-900">{word.word}</span>
              {word.phonetic && <span className="text-sm text-gray-400">/{word.phonetic}/</span>}
            </div>
            {word.senses.length === 0 ? (
              <p className="text-xs text-gray-400">未找到词义</p>
            ) : (
              word.senses.slice(0, 4).map((sense) => (
                <div key={sense.partOfSpeech + sense.definitions[0]?.definition} className="text-sm text-gray-700 leading-relaxed">
                  <span className="text-xs italic text-sky-600">{sense.partOfSpeech}</span>{' '}
                  {sense.definitions.map((d) => d.definition).join('；')}
                </div>
              ))
            )}
            {word.url && (
              <a href={word.url} target="_blank" rel="noreferrer" className="text-xs text-primary-600 hover:underline">
                在词典中查看更多
              </a>
            )}
          </div>
        ) : translation ? (
          <div className="space-y-2">
            <p className="text-xs text-gray-400">
              {translation.langpair === 'en|zh' ? '英 → 中' : '中 → 英'}翻译（MyMemory）
            </p>
            <p className="text-base text-gray-800 leading-relaxed">{translation.translated || '（无译文）'}</p>
            <p className="text-xs text-gray-400 mt-1">原文：{translation.source.slice(0, 120)}</p>
          </div>
        ) : null}
        {translation && !translation.translated && (
          <BookOpenText className="w-5 h-5 text-gray-300 mx-auto mt-3" />
        )}
      </div>
    </div>
  );
}