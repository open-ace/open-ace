/**
 * 生成兼容性 UUID v4
 *
 * 按以下优先级生成 UUID：
 * 1. crypto.randomUUID() - 现代浏览器原生支持
 * 2. crypto.getRandomValues() - 手动生成，兼容性好
 * 3. Math.random() - 最终 fallback，仅在不支持 crypto 的环境使用
 *
 * 解决 Issue #3364: crypto.randomUUID is not a function
 * 原因：旧版浏览器或非 HTTPS 环境下 crypto.randomUUID() 不可用
 */
export function generateUUID(): string {
  // 优先使用 crypto.randomUUID()
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    try {
      return crypto.randomUUID();
    } catch {
      // 某些环境下可能抛出异常（如非安全上下文），继续尝试 fallback
    }
  }

  // 使用 crypto.getRandomValues() 手动生成
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    try {
      const bytes = new Uint8Array(16);
      crypto.getRandomValues(bytes);
      // 设置 UUID v4 版本位 (第 6 字节高 4 位为 0100)
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      // 设置 UUID 变体位 (第 8 字节高 2 位为 10)
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      // 转换为 UUID 格式字符串
      const hex = Array.from(bytes)
        .map((b) => b.toString(16).padStart(2, '0'))
        .join('');
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
    } catch {
      // 继续尝试 fallback
    }
  }

  // 最终 fallback：Math.random()
  // 注意：Math.random() 不是加密安全的随机数生成器
  // 但对于幂等键生成场景足够使用
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}
