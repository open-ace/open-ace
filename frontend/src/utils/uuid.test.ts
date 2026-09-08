import { describe, it, expect, afterEach, vi } from 'vitest';

import { generateUUID } from './uuid';

// UUID v4 格式正则：xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx
// y 必须是 8, 9, a, 或 b
const UUID_V4_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

describe('generateUUID', () => {
  const originalCrypto = globalThis.crypto;

  afterEach(() => {
    // 恢复原始 crypto
    Object.defineProperty(globalThis, 'crypto', {
      value: originalCrypto,
      writable: true,
      configurable: true,
    });
  });

  it('should generate valid UUID v4 format', () => {
    const uuid = generateUUID();
    expect(uuid).toMatch(UUID_V4_REGEX);
  });

  it('should generate unique UUIDs', () => {
    const uuids = new Set<string>();
    for (let i = 0; i < 1000; i++) {
      uuids.add(generateUUID());
    }
    expect(uuids.size).toBe(1000);
  });

  it('should use crypto.randomUUID when available', () => {
    const mockRandomUUID = vi.fn().mockReturnValue('12345678-1234-4000-8000-123456789abc');
    Object.defineProperty(globalThis, 'crypto', {
      value: { randomUUID: mockRandomUUID },
      writable: true,
      configurable: true,
    });

    const uuid = generateUUID();
    expect(mockRandomUUID).toHaveBeenCalled();
    expect(uuid).toBe('12345678-1234-4000-8000-123456789abc');
  });

  it('should fallback to crypto.getRandomValues when randomUUID is not available', () => {
    // Mock crypto without randomUUID but with getRandomValues
    const mockGetRandomValues = vi.fn((arr: Uint8Array) => {
      // 填充伪随机值
      for (let i = 0; i < arr.length; i++) {
        arr[i] = i;
      }
      return arr;
    });

    Object.defineProperty(globalThis, 'crypto', {
      value: { getRandomValues: mockGetRandomValues },
      writable: true,
      configurable: true,
    });

    const uuid = generateUUID();
    expect(mockGetRandomValues).toHaveBeenCalled();
    expect(uuid).toMatch(UUID_V4_REGEX);
  });

  it('should fallback to Math.random when crypto is not available', () => {
    // 移除 crypto
    Object.defineProperty(globalThis, 'crypto', {
      value: undefined,
      writable: true,
      configurable: true,
    });

    const uuid = generateUUID();
    expect(uuid).toMatch(UUID_V4_REGEX);
  });

  it('should fallback to getRandomValues when randomUUID throws', () => {
    const mockRandomUUID = vi.fn().mockImplementation(() => {
      throw new Error('Not available in non-secure context');
    });
    const mockGetRandomValues = vi.fn((arr: Uint8Array) => {
      for (let i = 0; i < arr.length; i++) {
        arr[i] = Math.floor(Math.random() * 256);
      }
      return arr;
    });

    Object.defineProperty(globalThis, 'crypto', {
      value: {
        randomUUID: mockRandomUUID,
        getRandomValues: mockGetRandomValues,
      },
      writable: true,
      configurable: true,
    });

    const uuid = generateUUID();
    expect(mockRandomUUID).toHaveBeenCalled();
    expect(mockGetRandomValues).toHaveBeenCalled();
    expect(uuid).toMatch(UUID_V4_REGEX);
  });

  it('should set correct version and variant bits when using getRandomValues', () => {
    // 使用固定值测试版本位和变体位设置
    const mockGetRandomValues = vi.fn((arr: Uint8Array) => {
      // 所有字节设为 0
      for (let i = 0; i < arr.length; i++) {
        arr[i] = 0x00;
      }
      return arr;
    });

    Object.defineProperty(globalThis, 'crypto', {
      value: { getRandomValues: mockGetRandomValues },
      writable: true,
      configurable: true,
    });

    const uuid = generateUUID();

    // 解析 UUID 验证版本位和变体位
    const parts = uuid.split('-');

    // 第 7 字节（时间高位）的高 4 位应为版本号 4
    const versionByte = parseInt(parts[2].slice(0, 2), 16);
    expect((versionByte >> 4) & 0xf).toBe(4);

    // 第 9 字节（时钟序列）的高 2 位应为 10（变体 RFC 4122）
    const variantByte = parseInt(parts[3].slice(0, 2), 16);
    expect((variantByte >> 6) & 0x3).toBe(0b10);
  });
});
