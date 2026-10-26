/** 本地时区日界工具：避免用 toISOString().slice(0,10) 把「今天」算成 UTC 日期。 */

export function startOfLocalDay(date: Date = new Date()): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

export function localDayKey(date: Date = new Date()): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

/** 本地零点对应的 UTC ISO 字符串，用于后端 since 参数。 */
export function localDayStartIso(date: Date = new Date()): string {
  return startOfLocalDay(date).toISOString();
}
