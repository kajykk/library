'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { ArrowLeft, FolderSearch, Upload, AlertTriangle, Copy, FileX, CheckCircle2, Loader2 } from 'lucide-react';

interface ScanResult {
  total: number;
  importable: Array<{ path: string; name: string; size: number; ext: string }>;
  bad_files: Array<{ path: string; size: number; reason: string }>;
  duplicates: Array<{ path: string; size: number; duplicate_of: string }>;
  unsupported: Array<{ path: string; size: number; ext: string }>;
  truncated: boolean;
  directories: Record<string, { total: number; importable: number; bad_files: number; duplicates: number; unsupported: number }>;
}

interface ImportResult {
  imported: number;
  skipped: number;
  failed: number;
  results: Array<{ path: string; status: string; reason?: string; document_id?: string }>;
}

type Step = 'scan' | 'review' | 'result';

export default function ImportPage() {
  const [step, setStep] = useState<Step>('scan');
  const [scanData, setScanData] = useState<ScanResult | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleScan = async () => {
    try {
      setError(null);
      const resp = await fetch('/api/imports/scan', {
        headers: { 'X-API-Token': localStorage.getItem('kb_api_token') || '' },
      });
      if (!resp.ok) throw new Error(`扫描失败：${resp.status}`);
      const data: ScanResult = await resp.json();
      setScanData(data);
      setSelected(new Set(data.importable.map((f) => f.path)));
      setStep('review');
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const handleImport = async () => {
    if (!scanData || selected.size === 0) return;
    try {
      setImporting(true);
      setError(null);
      const resp = await fetch('/api/imports/execute', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Token': localStorage.getItem('kb_api_token') || '',
        },
        body: JSON.stringify({ paths: Array.from(selected), confirm: true }),
      });
      if (!resp.ok) throw new Error(`导入失败：${resp.status}`);
      const result: ImportResult = await resp.json();
      setImportResult(result);
      setStep('result');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setImporting(false);
    }
  };

  const toggleSelect = (path: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white shadow-sm border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between h-16">
          <div className="flex items-center gap-3">
            <Link href="/library" className="p-2 hover:bg-gray-100 rounded-lg transition-colors">
              <ArrowLeft className="w-5 h-5" />
            </Link>
            <h1 className="text-xl font-bold text-gray-900">导入中心</h1>
          </div>
          {/* 步骤指示 */}
          <div className="flex items-center gap-2 text-sm">
            {(['scan', 'review', 'result'] as Step[]).map((s, i) => (
              <span key={s} className={`flex items-center gap-1 ${step === s ? 'text-primary-600 font-medium' : 'text-gray-400'}`}>
                {i + 1}. {{ scan: '扫描', review: '确认', result: '完成' }[s]}
                {i < 2 && <span className="mx-1 text-gray-300">→</span>}
              </span>
            ))}
          </div>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {error && (
          <div className="mb-4 bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">{error}</div>
        )}

        {/* 步骤 1：扫描 */}
        {step === 'scan' && (
          <div className="bg-white rounded-lg border border-gray-200 p-8 text-center">
            <FolderSearch className="w-16 h-16 text-gray-300 mx-auto mb-4" />
            <h2 className="text-lg font-semibold text-gray-900 mb-2">扫描导入目录</h2>
            <p className="text-gray-500 mb-6 max-w-md mx-auto">
              扫描白名单目录中的 EPUB/PDF/MOBI/TXT 文件，自动分类坏文件与重复文件。
            </p>
            <button
              onClick={handleScan}
              className="px-6 py-3 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors font-medium"
            >
              开始扫描
            </button>
          </div>
        )}

        {/* 步骤 2：确认 */}
        {step === 'review' && scanData && (
          <div className="space-y-4">
            <div className="bg-white rounded-lg border border-gray-200 p-4">
              <div className="flex items-center justify-between mb-3">
                <h2 className="font-semibold text-gray-900">扫描结果</h2>
                <div className="flex items-center gap-2">
                  <button onClick={() => setSelected(new Set(scanData.importable.map((f) => f.path)))} className="text-sm text-primary-600">全选</button>
                  <button onClick={() => setSelected(new Set())} className="text-sm text-gray-500">全不选</button>
                </div>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                <div className="text-center p-3 bg-green-50 rounded-lg">
                  <p className="text-2xl font-bold text-green-600">{scanData.importable.length}</p>
                  <p className="text-xs text-gray-500">可导入</p>
                </div>
                <div className="text-center p-3 bg-red-50 rounded-lg">
                  <p className="text-2xl font-bold text-red-600">{scanData.bad_files.length}</p>
                  <p className="text-xs text-gray-500">坏文件</p>
                </div>
                <div className="text-center p-3 bg-amber-50 rounded-lg">
                  <p className="text-2xl font-bold text-amber-600">{scanData.duplicates.length}</p>
                  <p className="text-xs text-gray-500">重复</p>
                </div>
                <div className="text-center p-3 bg-gray-50 rounded-lg">
                  <p className="text-2xl font-bold text-gray-600">{scanData.unsupported.length}</p>
                  <p className="text-xs text-gray-500">不支持</p>
                </div>
              </div>
              {scanData.truncated && (
                <div className="text-sm text-amber-600 mb-3">文件数量超过上限，结果已截断</div>
              )}
            </div>

            {/* 可导入文件列表 */}
            <div className="bg-white rounded-lg border border-gray-200">
              <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between">
                <h3 className="font-medium text-gray-900">可导入文件（已选 {selected.size}/{scanData.importable.length}）</h3>
                <button
                  onClick={handleImport}
                  disabled={selected.size === 0 || importing}
                  className="flex items-center gap-2 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 transition-colors"
                >
                  {importing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
                  导入 {selected.size} 个文件
                </button>
              </div>
              <div className="max-h-96 overflow-y-auto">
                {scanData.importable.map((f) => (
                  <label key={f.path} className="flex items-center gap-3 px-4 py-2 hover:bg-gray-50 cursor-pointer border-b border-gray-50">
                    <input
                      type="checkbox"
                      checked={selected.has(f.path)}
                      onChange={() => toggleSelect(f.path)}
                      className="rounded"
                    />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-gray-900 truncate">{f.name}</p>
                      <p className="text-xs text-gray-400">{(f.size / 1024 / 1024).toFixed(1)} MB · {f.ext.toUpperCase()}</p>
                    </div>
                  </label>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* 步骤 3：完成 */}
        {step === 'result' && importResult && (
          <div className="bg-white rounded-lg border border-gray-200 p-8 text-center">
            <CheckCircle2 className="w-16 h-16 text-green-400 mx-auto mb-4" />
            <h2 className="text-lg font-semibold text-gray-900 mb-2">导入完成</h2>
            <div className="flex items-center justify-center gap-6 mb-6">
              <div>
                <p className="text-3xl font-bold text-green-600">{importResult.imported}</p>
                <p className="text-sm text-gray-500">成功</p>
              </div>
              <div>
                <p className="text-3xl font-bold text-amber-600">{importResult.skipped}</p>
                <p className="text-sm text-gray-500">跳过</p>
              </div>
              <div>
                <p className="text-3xl font-bold text-red-600">{importResult.failed}</p>
                <p className="text-sm text-gray-500">失败</p>
              </div>
            </div>
            <Link href="/library" className="px-6 py-3 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors font-medium">
              返回书架
            </Link>
          </div>
        )}
      </main>
    </div>
  );
}
