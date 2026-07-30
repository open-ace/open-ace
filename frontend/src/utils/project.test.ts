/**
 * Tests for project path utilities
 */

import { describe, it, expect } from 'vitest';
import { decodeProjectName, encodeProjectPath } from './project';

describe('decodeProjectName', () => {
  describe('b64 format', () => {
    it('should decode b64 encoded path', () => {
      // Encode /home/user/demo-project
      const encoded = 'b64:L2hvbWUvdXNlci9kZW1vLXByb2plY3Q';
      const decoded = decodeProjectName(encoded);
      expect(decoded).toBe('/home/user/demo-project');
    });

    it('should handle b64 format with padding', () => {
      // Test with different path lengths that may need padding
      const encoded = 'b64:L2hvbWUvdXNlci9wcm9qZWN0'; // /home/user/project
      const decoded = decodeProjectName(encoded);
      expect(decoded).toBe('/home/user/project');
    });

    it('should return original string if b64 decode fails', () => {
      const invalidB64 = 'b64:!!invalid!!';
      const decoded = decodeProjectName(invalidB64);
      expect(decoded).toBe(invalidB64);
    });
  });

  describe('legacy format', () => {
    it('should decode legacy format -home-user-project', () => {
      const encoded = '-home-user-demo-project';
      const decoded = decodeProjectName(encoded);
      expect(decoded).toBe('/home/user/demo/project');
    });

    it('should handle legacy format with single segment', () => {
      const encoded = '-home';
      const decoded = decodeProjectName(encoded);
      expect(decoded).toBe('/home');
    });

    it('should handle legacy format with deep path', () => {
      const encoded = '-home-user-workspace-my-project';
      const decoded = decodeProjectName(encoded);
      expect(decoded).toBe('/home/user/workspace/my/project');
    });
  });

  describe('unencoded format', () => {
    it('should return unencoded path as-is', () => {
      const path = '/home/user/project';
      const decoded = decodeProjectName(path);
      expect(decoded).toBe(path);
    });

    it('should handle Windows-style paths', () => {
      const path = 'C:/Users/demo/project';
      const decoded = decodeProjectName(path);
      expect(decoded).toBe(path);
    });
  });

  describe('edge cases', () => {
    it('should return empty string for empty input', () => {
      expect(decodeProjectName('')).toBe('');
    });

    it('should handle paths with special characters', () => {
      // Test path with hyphens in actual path
      const encoded = '-home-user-my-project'; // Legacy format
      const decoded = decodeProjectName(encoded);
      expect(decoded).toBe('/home/user/my/project');
    });
  });
});

describe('encodeProjectPath', () => {
  it('should encode a simple path', () => {
    const path = '/home/user/demo-project';
    const encoded = encodeProjectPath(path);
    expect(encoded).toMatch(/^b64:/);
    
    // Verify decode returns original
    const decoded = decodeProjectName(encoded);
    expect(decoded).toBe(path);
  });

  it('should return empty string for empty input', () => {
    expect(encodeProjectPath('')).toBe('');
  });

  it('should normalize Windows paths', () => {
    const windowsPath = '/C:/Users/demo/project';
    const encoded = encodeProjectPath(windowsPath);
    const decoded = decodeProjectName(encoded);
    expect(decoded).toBe('C:/Users/demo/project');
  });

  it('should produce decodable output', () => {
    const testPaths = [
      '/home/user/project',
      '/var/www/html',
      '/Users/test/workspace',
    ];

    for (const path of testPaths) {
      const encoded = encodeProjectPath(path);
      const decoded = decodeProjectName(encoded);
      expect(decoded).toBe(path);
    }
  });
});

describe('encode/decode roundtrip', () => {
  it('should maintain consistency for various paths', () => {
    const testPaths = [
      '/home/user/open-ace',
      '/var/lib/docker',
      '/Users/admin/projects/test-project',
      '/srv/repo',
    ];

    for (const path of testPaths) {
      const encoded = encodeProjectPath(path);
      const decoded = decodeProjectName(encoded);
      expect(decoded).toBe(path);
    }
  });
});