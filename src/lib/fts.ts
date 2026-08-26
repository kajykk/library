/**
 * SQLite FTS5 MATCH 查询转义：整词包引号并双写内部引号，防止语法注入。
 * 与后端 server/app/routers/search.py 中 _escape_fts_query 保持语义一致，
 * 前端侧用于展示/拼接搜索语法的场景。
 */
export function escapeFtsQuery(q: string): string {
  // 空串产出 `""` 会被 FTS5 判为语法错误（syntax error near """"），
  // 调用方应先过滤空查询；此处兜底返回空串而非非法表达式
  if (!q) return '';
  return '"' + q.replace(/"/g, '""') + '"';
}
