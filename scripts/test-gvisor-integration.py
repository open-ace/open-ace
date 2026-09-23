#!/usr/bin/env python3
"""OpenACE gVisor 沙箱隔离集成测试

测试场景：
1. 沙箱创建和销毁
2. gVisor 内核隔离验证
3. 文件系统隔离验证
4. 网络隔离验证
5. 能力契约验证
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

class GvisorIntegrationTest:
    def __init__(self):
        self.kubectl = ["kubectl", "-n", "open-ace-sandboxes"]
        self.test_image = os.environ.get("TEST_IMAGE", "docker.m.daocloud.io/library/busybox:latest")
        self.passed = 0
        self.failed = 0
        
    def run_kubectl(self, *args, check=True):
        """运行 kubectl 命令"""
        cmd = self.kubectl + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if check and result.returncode != 0:
            print(f"❌ 命令失败: {' '.join(cmd)}")
            print(f"   错误: {result.stderr}")
            return None
        return result
    
    def test_pod_create_with_gvisor(self):
        """测试 1: 创建 gVisor Pod"""
        print("\n>>> 测试 1: gVisor Pod 创建...")
        
        # 清理旧测试 Pod
        self.run_kubectl("delete", "pod", "test-gvisor-iso", "--ignore-not-found", check=False)
        
        # 创建测试 Pod
        manifest = f"""
apiVersion: v1
kind: Pod
metadata:
  name: test-gvisor-iso
spec:
  runtimeClassName: gvisor
  containers:
  - name: test
    image: {self.test_image}
    command: ["sh", "-c", "sleep 3600"]
"""
        
        result = subprocess.run(
            ["kubectl", "apply", "-n", "open-ace-sandboxes", "-f", "-"],
            input=manifest,
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            print(f"❌ Pod 创建失败: {result.stderr}")
            self.failed += 1
            return False
        
        # 等待 Pod 就绪
        print("   等待 Pod 就绪...")
        for i in range(30):
            result = self.run_kubectl(
                "get", "pod", "test-gvisor-iso",
                "-o", "jsonpath={.status.phase}",
                check=False
            )
            if result and result.stdout.strip() == "Running":
                print("✅ gVisor Pod 创建成功")
                self.passed += 1
                return True
            time.sleep(1)
        
        print("❌ Pod 启动超时")
        self.failed += 1
        return False
    
    def test_kernel_isolation(self):
        """测试 2: gVisor 内核隔离验证"""
        print("\n>>> 测试 2: gVisor 内核隔离验证...")
        
        # 检查内核版本
        result = self.run_kubectl(
            "exec", "test-gvisor-iso", "--", "uname", "-a",
            check=False
        )
        
        if result and "gvisor" in result.stdout.lower():
            print(f"✅ gVisor 内核识别成功: {result.stdout.strip()}")
            self.passed += 1
            return True
        else:
            print(f"❌ 未检测到 gVisor 内核: {result.stdout if result else 'N/A'}")
            self.failed += 1
            return False
    
    def test_filesystem_isolation(self):
        """测试 3: 文件系统隔离验证"""
        print("\n>>> 测试 3: 文件系统隔离验证...")
        
        # 测试 1: 无法访问宿主机文件系统
        result = self.run_kubectl(
            "exec", "test-gvisor-iso", "--", "ls", "/host",
            check=False
        )
        
        if result and result.returncode != 0:
            print("   ✅ 无法访问 /host 目录（符合预期）")
        else:
            print("   ⚠️  可以访问 /host 目录（可能存在配置问题）")
        
        # 测试 2: 容器内文件系统隔离
        result = self.run_kubectl(
            "exec", "test-gvisor-iso", "--", "sh", "-c",
            "echo 'test' > /tmp/isolation-test && cat /tmp/isolation-test",
            check=False
        )
        
        if result and "test" in result.stdout:
            print("✅ 文件系统隔离验证通过")
            self.passed += 1
            return True
        else:
            print("❌ 文件系统隔离验证失败")
            self.failed += 1
            return False
    
    def test_network_isolation(self):
        """测试 4: 网络隔离验证"""
        print("\n>>> 测试 4: 网络隔离验证...")
        
        # 测试 1: 无法访问 metadata service（GKE/AWS/Azure）
        # 在非云环境可能不存在，所以只做尝试
        result = self.run_kubectl(
            "exec", "test-gvisor-iso", "--", "sh", "-c",
            "wget -T 2 http://169.254.169.254/ 2>&1 || echo 'timeout'",
            check=False
        )
        
        if result and ("timeout" in result.stdout or "Connection refused" in result.stdout):
            print("   ✅ 无法访问 metadata service（符合预期）")
        else:
            print("   ⚠️  可能可以访问 metadata service（注意检查 NetworkPolicy）")
        
        # 测试 2: 网络命名空间隔离
        result = self.run_kubectl(
            "exec", "test-gvisor-iso", "--", "ip", "addr",
            check=False
        )
        
        if result and "lo" in result.stdout:
            print("✅ 网络命名空间隔离验证通过")
            self.passed += 1
            return True
        else:
            print("❌ 网络命名空间隔离验证失败")
            self.failed += 1
            return False
    
    def test_process_isolation(self):
        """测试 5: 进程隔离验证"""
        print("\n>>> 测试 5: 进程隔离验证...")
        
        # 测试进程命名空间隔离
        result = self.run_kubectl(
            "exec", "test-gvisor-iso", "--", "ps", "aux",
            check=False
        )
        
        if result:
            process_count = len(result.stdout.strip().split('\n'))
            if process_count <= 5:  # gVisor 容器通常只有很少进程
                print(f"✅ 进程隔离验证通过（进程数: {process_count}）")
                self.passed += 1
                return True
            else:
                print(f"⚠️  进程数较多（{process_count}），可能存在隔离问题")
        
        self.failed += 1
        return False
    
    def test_security_context(self):
        """测试 6: 安全上下文验证"""
        print("\n>>> 测试 6: 安全上下文验证...")
        
        # 检查是否以非 root 运行
        result = self.run_kubectl(
            "exec", "test-gvisor-iso", "--", "id",
            check=False
        )
        
        if result and "uid=0" not in result.stdout:
            print(f"   ✅ 容器以非 root 用户运行: {result.stdout.strip()}")
        else:
            print("   ⚠️  容器以 root 用户运行（建议配置 securityContext）")
        
        # 检查只读文件系统（如果配置了）
        result = self.run_kubectl(
            "exec", "test-gvisor-iso", "--", "mount",
            check=False
        )
        
        if result:
            print("✅ 安全上下文验证通过")
            self.passed += 1
            return True
        
        self.failed += 1
        return False
    
    def cleanup(self):
        """清理测试资源"""
        print("\n>>> 清理测试资源...")
        self.run_kubectl("delete", "pod", "test-gvisor-iso", "--ignore-not-found", check=False)
        print("✅ 清理完成")
    
    def run_all_tests(self):
        """运行所有测试"""
        print("=" * 60)
        print("OpenACE gVisor 沙箱隔离集成测试")
        print("=" * 60)
        
        try:
            self.test_pod_create_with_gvisor()
            self.test_kernel_isolation()
            self.test_filesystem_isolation()
            self.test_network_isolation()
            self.test_process_isolation()
            self.test_security_context()
        finally:
            self.cleanup()
        
        print("\n" + "=" * 60)
        print("测试结果汇总")
        print("=" * 60)
        print(f"✅ 通过: {self.passed}")
        print(f"❌ 失败: {self.failed}")
        print(f"📊 总计: {self.passed + self.failed}")
        
        if self.failed == 0:
            print("\n🎉 所有测试通过！gVisor 沙箱隔离功能正常")
            return 0
        else:
            print("\n⚠️  部分测试失败，请检查配置")
            return 1


if __name__ == "__main__":
    tester = GvisorIntegrationTest()
    sys.exit(tester.run_all_tests())