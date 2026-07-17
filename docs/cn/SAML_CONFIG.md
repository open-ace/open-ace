# SAML 2.0 SSO 配置

Open ACE 可以作为 SAML 2.0 Service Provider（SP）接入企业 IdP。

## 端点

| 端点 | 用途 |
|------|------|
| `GET /api/sso/login/<provider_name>` | 生成 SAML AuthnRequest 并跳转到 IdP |
| `POST /api/sso/acs/<provider_name>` | HTTP-POST SAMLResponse 回调的 ACS |
| `GET /api/sso/providers/<provider_name>/metadata` | 供 IdP 配置使用的 SP metadata XML |

## Provider 配置

通过 `POST /api/sso/providers` 注册 SAML Provider：

```json
{
  "name": "corp-saml",
  "provider_type": "saml",
  "client_id": "https://openace.example.com/saml/metadata",
  "authorization_url": "https://idp.example.com/sso",
  "redirect_uri": "https://openace.example.com/api/sso/acs/corp-saml",
  "issuer_url": "https://idp.example.com/metadata",
  "extra_params": {
    "idp_x509_cert": "MIIC...",
    "idp_entity_id": "https://idp.example.com/metadata",
    "attribute_mapping": {
      "email": "email",
      "username": "uid",
      "name": "displayName"
    }
  }
}
```

`client_id` 是 SP entity ID。SAML Provider 不要求 `client_secret`。
IdP 配置可以直接提供 `authorization_url`、`issuer_url`、`idp_x509_cert`，
也可以通过 `extra_params.idp_metadata_xml` 或 `extra_params.idp_metadata_url` 提供。

## 校验

ACS 会校验：

- 使用配置的 IdP 证书验证 XML Signature
- SAML success status
- IdP issuer
- audience restriction 是否匹配 SP entity ID
- response destination 与 subject recipient 是否匹配 ACS URL
- `InResponseTo` 是否匹配 `RelayState` 绑定的请求
- assertion 时间窗口和小范围时钟偏移
- 必需属性，默认要求 email

## 边界

Open ACE 当前支持 Redirect binding 的 AuthnRequest，以及 HTTP-POST binding 的
ACS。只要签名与配置的 IdP 证书匹配，签名在 Response 或 Assertion 上均可接受。
本地用户创建和身份关联复用 OAuth2/OIDC 使用的 SSO identity 表。
