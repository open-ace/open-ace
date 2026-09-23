# OpenACE gVisor 沙箱功能资源清单

**用途**: 供离线部署使用
**存放位置**: 192.168.1.21:/datastore/open-ace/gVisor/packages

---

## 目录结构

```
/datastore/open-ace/gVisor/
├── packages/
│   ├── images/
│   │   ├── opensandbox-controller-latest.tar
│   │   ├── opensandbox-server-v0.2.3.tar
│   │   ├── opensandbox-execd-v1.1.0.tar
│   │   ├── opensandbox-ingress-latest.tar
│   │   └── open-ace-webui-sandbox-latest.tar
│   ├── binaries/
│   │   └── runsc (gVisor 二进制)
│   ├── configs/
│   │   ├── opensandbox-controller.yaml
│   │   ├── local-storage.yaml
│   │   ├── crds.yaml
│   │   └── rbac.yaml
│   └── scripts/
│       ├── deploy-gvisor.sh
│       └── verify-gvisor.sh
└── docs/
    └── gvisor-sandbox-deployment-guide.md
```

---

## 镜像导出命令

```bash
# 在有网络访问的机器上执行

# 创建目录
mkdir -p /datastore/open-ace/gVisor/packages/{images,binaries,configs,scripts}

# 导出 OpenSandbox 镜像
ctr -n k8s.io image export /datastore/open-ace/gVisor/packages/images/opensandbox-controller-latest.tar \
  docker.m.daocloud.io/opensandbox/controller:latest

ctr -n k8s.io image export /datastore/open-ace/gVisor/packages/images/opensandbox-server-v0.2.3.tar \
  docker.m.daocloud.io/opensandbox/server:v0.2.3

ctr -n k8s.io image export /datastore/open-ace/gVisor/packages/images/opensandbox-execd-v1.1.0.tar \
  docker.m.daocloud.io/opensandbox/execd:v1.1.0

ctr -n k8s.io image export /datastore/open-ace/gVisor/packages/images/opensandbox-ingress-latest.tar \
  docker.m.daocloud.io/opensandbox/ingress:latest

# 复制 gVisor 二进制
cp /usr/local/bin/runsc /datastore/open-ace/gVisor/packages/binaries/
```

---

## 镜像导入命令

```bash
# 在目标机器上执行

# 导入镜像
ctr -n k8s.io image import /datastore/open-ace/gVisor/packages/images/opensandbox-controller-latest.tar
ctr -n k8s.io image import /datastore/open-ace/gVisor/packages/images/opensandbox-server-v0.2.3.tar
ctr -n k8s.io image import /datastore/open-ace/gVisor/packages/images/opensandbox-execd-v1.1.0.tar
ctr -n k8s.io image import /datastore/open-ace/gVisor/packages/images/opensandbox-ingress-latest.tar

# 安装 gVisor
cp /datastore/open-ace/gVisor/packages/binaries/runsc /usr/local/bin/
chmod +x /usr/local/bin/runsc
```

---

## 镜像信息

| 镜像 | Digest | Size |
|------|--------|------|
| opensandbox/controller:latest | sha256:6d406ae0b89151b1dcf661781590108fd51fbdb35dbb0ed72c9b96596aaaf702 | 97.5 MiB |
| opensandbox/server:v0.2.3 | sha256:ae8dfbb277f40a39ff01ef35e5e1c10675acfe0fa9db15259b8f323e5efab778 | 61.7 MiB |
| opensandbox/execd:v1.1.0 | sha256:6cf7dba2f21f0b536e100563d841ac58a9f31c2b0a081b7ac76796a24d6f47e2 | 57.5 MiB |
| opensandbox/ingress:latest | sha256:17ea848cbc09f23e0165651e3506ea7535a53a1b2020b2e5bf77de915ea698d4 | 32.0 MiB |

---

## 配置文件清单

见项目 `scripts/` 目录下的文件：
- `opensandbox-controller.yaml`
- `local-storage.yaml`
- `opensandbox-pvc.yaml`