'use client';

/**
 * 阅读器 TTS 听书（对标 Readest/微信读书）：基于 Web Speech API，零服务端成本。
 * 按段落顺序朗读，当前段落高亮跟随并滚动到视口；支持语速调节、暂停/恢复、停止。
 * 浏览器不支持（jsdom/老 webview）时 supported=false，调用方隐藏入口。
 */

import { useCallback, useEffect, useRef, useState } from 'react';

const ACTIVE_CLASS = 'kb-tts-active';

export interface UseTtsResult {
  supported: boolean;
  speaking: boolean;
  paused: boolean;
  rate: number;
  activeIndex: number | null;
  start: () => void;
  stop: () => void;
  togglePause: () => void;
  setRate: (rate: number) => void;
}

export function useTts(
  getParagraphs: () => HTMLElement[],
  onFinished?: () => void,
): UseTtsResult {
  const supported =
    typeof window !== 'undefined' &&
    'speechSynthesis' in window &&
    typeof SpeechSynthesisUtterance !== 'undefined';

  const [speaking, setSpeaking] = useState(false);
  const [paused, setPaused] = useState(false);
  const [rate, setRate] = useState(1);
  const [activeIndex, setActiveIndex] = useState<number | null>(null);

  const rateRef = useRef(rate);
  // 会话 id：stop/新 start 时递增，旧 utterance 的异步回调据此失效，
  // 避免 cancel() 触发的 onend 继续把旧链条念下去
  const sessionRef = useRef(0);
  const paragraphsRef = useRef<HTMLElement[]>([]);
  const onFinishedRef = useRef(onFinished);

  useEffect(() => {
    rateRef.current = rate;
  }, [rate]);

  useEffect(() => {
    onFinishedRef.current = onFinished;
  }, [onFinished]);

  const clearActive = useCallback(() => {
    document.querySelectorAll(`.${ACTIVE_CLASS}`).forEach((el) => el.classList.remove(ACTIVE_CLASS));
  }, []);

  // 卸载时停止朗读并清理段落高亮
  useEffect(() => {
    return () => {
      sessionRef.current += 1;
      if (supported) window.speechSynthesis.cancel();
      clearActive();
    };
  }, [supported, clearActive]);

  const speakFrom = useCallback(
    (index: number, session: number) => {
      if (session !== sessionRef.current) return;
      const paragraphs = paragraphsRef.current;
      if (index >= paragraphs.length) {
        // 全部段落读完：自然结束
        sessionRef.current += 1;
        setSpeaking(false);
        setPaused(false);
        setActiveIndex(null);
        clearActive();
        onFinishedRef.current?.();
        return;
      }
      setActiveIndex(index);
      clearActive();
      const el = paragraphs[index];
      if (!el) return;
      el.classList.add(ACTIVE_CLASS);
      el.scrollIntoView?.({ behavior: 'smooth', block: 'center' });

      const text = (el.textContent || '').trim();
      if (!text) {
        speakFrom(index + 1, session);
        return;
      }
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = rateRef.current;
      utterance.onend = () => speakFrom(index + 1, session);
      utterance.onerror = () => speakFrom(index + 1, session);
      window.speechSynthesis.speak(utterance);
    },
    [clearActive],
  );

  const start = useCallback(() => {
    if (!supported) return;
    const paragraphs = getParagraphs().filter(Boolean);
    if (paragraphs.length === 0) return;
    sessionRef.current += 1;
    window.speechSynthesis.cancel();
    paragraphsRef.current = paragraphs;
    setSpeaking(true);
    setPaused(false);
    const session = sessionRef.current;
    // cancel() 会异步触发旧 utterance 的 onend/onerror，延后一拍避免旧回调串台
    setTimeout(() => speakFrom(0, session), 50);
  }, [supported, getParagraphs, speakFrom]);

  const stop = useCallback(() => {
    sessionRef.current += 1;
    if (supported) window.speechSynthesis.cancel();
    setSpeaking(false);
    setPaused(false);
    setActiveIndex(null);
    clearActive();
  }, [supported, clearActive]);

  const togglePause = useCallback(() => {
    if (!supported || !speaking) return;
    if (paused) {
      window.speechSynthesis.resume();
      setPaused(false);
    } else {
      window.speechSynthesis.pause();
      setPaused(true);
    }
  }, [supported, speaking, paused]);

  return { supported, speaking, paused, rate, activeIndex, start, stop, togglePause, setRate };
}
