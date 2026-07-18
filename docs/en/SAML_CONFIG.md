# SAML 2.0 SSO Configuration

Open ACE can act as a SAML 2.0 Service Provider (SP) for enterprise IdPs.

## Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /api/sso/login/<provider_name>` | Generates a SAML AuthnRequest and redirects to the IdP |
| `POST /api/sso/acs/<provider_name>` | Assertion Consumer Service for HTTP-POST SAMLResponse callbacks |
| `GET /api/sso/providers/<provider_name>/metadata` | SP metadata XML for IdP configuration |

## Provider Configuration

Register a SAML provider through `POST /api/sso/providers`:

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

`client_id` is the SP entity ID. SAML providers do not require `client_secret`.
IdP configuration can be supplied directly with `authorization_url`, `issuer_url`,
and `idp_x509_cert`, or via `extra_params.idp_metadata_xml` /
`extra_params.idp_metadata_url`.

## Validation

The ACS handler validates:

- XML Signature with the configured IdP certificate
- SAML success status
- IdP issuer
- audience restriction matching the SP entity ID
- response destination and subject recipient matching the ACS URL
- `InResponseTo` matching the request bound to `RelayState`
- assertion time windows with a small clock skew
- required attributes, including email by default

## Boundaries

Open ACE currently supports Redirect binding for AuthnRequest and HTTP-POST binding
for ACS. Signed assertions or signed responses are accepted when the signature
matches the configured IdP certificate. Local user provisioning/linking follows the
same SSO identity table used by OAuth2/OIDC.
