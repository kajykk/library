/**
 * 模糊匹配打分：query 的每个字符按顺序出现在 target 中才得分，
 * 连续命中（streak）加成更高，target 越长略微降分。
 * 返回 null 表示 target 不包含 query 的字符序列。
 */
export function fuzzyScore(query: string, target: string): number | null {
  const q = query.toLowerCase();
  const t = target.toLowerCase();
  if (!q) return 1;
  let score = 0;
  let ti = 0;
  let streak = 0;
  for (const ch of q) {
    const idx = t.indexOf(ch, ti);
    if (idx < 0) return null;
    if (idx === ti) {
      streak += 1;
      score += streak >= 2 ? 4 : 2;
    } else {
      streak = 0;
      score += 1;
    }
    ti = idx + 1;
  }
  return score - t.length * 0.01;
}
