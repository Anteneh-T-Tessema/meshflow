"""MeshFlow SSO Provider Helpers — pre-configured OIDCConfig factories.

Each helper builds a ready-to-use :class:`~meshflow.security.oidc.OIDCConfig`
from the minimum credentials required for each well-known identity provider.

Usage::

    from meshflow.security.sso_providers import OktaConfig, Auth0Config

    cfg = OktaConfig(domain="dev-123456.okta.com", audience="meshflow-api")
    # cfg is an OIDCConfig — pass it to OIDCValidator / setup_oidc_middleware
"""

from __future__ import annotations

from meshflow.security.oidc import OIDCConfig


def OktaConfig(
    domain: str,
    audience: str = "meshflow-api",
    *,
    authorization_server: str = "default",
    role_claim: str = "groups",
    admin_group: str = "meshflow-admins",
    operator_group: str = "meshflow-operators",
    viewer_group: str = "meshflow-viewers",
    jwks_cache_ttl: int = 3600,
) -> OIDCConfig:
    """Return an :class:`OIDCConfig` pre-configured for Okta.

    Parameters
    ----------
    domain:
        Your Okta domain, e.g. ``dev-123456.okta.com``.
    audience:
        The API audience defined in Okta (default: ``meshflow-api``).
    authorization_server:
        Okta authorization server ID (default: ``default``).
        Pass the server ID string for custom authorization servers.
    """
    domain = domain.rstrip("/")
    # Okta issues tokens from the authorization server URL, not the root domain
    issuer = f"https://{domain}/oauth2/{authorization_server}"
    return OIDCConfig(
        issuer=issuer,
        audience=audience,
        role_claim=role_claim,
        admin_group=admin_group,
        operator_group=operator_group,
        viewer_group=viewer_group,
        jwks_cache_ttl=jwks_cache_ttl,
    )


def Auth0Config(
    domain: str,
    audience: str = "meshflow-api",
    *,
    role_claim: str = "https://meshflow.io/roles",
    admin_group: str = "meshflow-admins",
    operator_group: str = "meshflow-operators",
    viewer_group: str = "meshflow-viewers",
    jwks_cache_ttl: int = 3600,
) -> OIDCConfig:
    """Return an :class:`OIDCConfig` pre-configured for Auth0.

    Parameters
    ----------
    domain:
        Your Auth0 domain, e.g. ``your-tenant.auth0.com`` or
        ``your-tenant.us.auth0.com`` (no protocol prefix).
    audience:
        The API identifier registered in Auth0 (default: ``meshflow-api``).
    role_claim:
        JWT claim name for roles.  Auth0 uses namespaced custom claims, so the
        default value is ``https://meshflow.io/roles``.  Match this to the
        Action/Rule you configure in Auth0 to add roles to tokens.
    """
    domain = domain.rstrip("/")
    # Auth0 issues from https://<domain>/
    issuer = f"https://{domain}/"
    return OIDCConfig(
        issuer=issuer,
        audience=audience,
        role_claim=role_claim,
        admin_group=admin_group,
        operator_group=operator_group,
        viewer_group=viewer_group,
        jwks_cache_ttl=jwks_cache_ttl,
    )


def AzureADConfig(
    tenant_id: str,
    client_id: str,
    *,
    role_claim: str = "roles",
    admin_group: str = "meshflow-admins",
    operator_group: str = "meshflow-operators",
    viewer_group: str = "meshflow-viewers",
    jwks_cache_ttl: int = 3600,
    v2: bool = True,
) -> OIDCConfig:
    """Return an :class:`OIDCConfig` pre-configured for Microsoft Azure AD / Entra ID.

    Parameters
    ----------
    tenant_id:
        Azure AD tenant ID (UUID), e.g. ``xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx``.
    client_id:
        Application (client) ID registered in Azure AD — used as the audience.
    v2:
        Use v2.0 endpoint (default True).  Set to False for v1.0 (legacy apps).
    """
    endpoint = "v2.0" if v2 else ""
    if endpoint:
        issuer = f"https://login.microsoftonline.com/{tenant_id}/{endpoint}"
    else:
        issuer = f"https://sts.windows.net/{tenant_id}"
    return OIDCConfig(
        issuer=issuer,
        audience=client_id,
        role_claim=role_claim,
        admin_group=admin_group,
        operator_group=operator_group,
        viewer_group=viewer_group,
        jwks_cache_ttl=jwks_cache_ttl,
    )


def GoogleWorkspaceConfig(
    client_id: str,
    *,
    role_claim: str = "hd",
    admin_group: str = "meshflow-admins",
    operator_group: str = "meshflow-operators",
    viewer_group: str = "meshflow-viewers",
    jwks_cache_ttl: int = 3600,
) -> OIDCConfig:
    """Return an :class:`OIDCConfig` pre-configured for Google Workspace / Google Identity.

    Parameters
    ----------
    client_id:
        OAuth 2.0 client ID from the Google Cloud Console.
    role_claim:
        JWT claim used for role mapping.  The default ``hd`` (hosted domain) is
        the hosted-domain claim, suitable for domain-level access control.
        For finer-grained roles, set a custom claim name and populate it via
        Google's token-customization features or an ID token enrichment layer.
    """
    return OIDCConfig(
        issuer="https://accounts.google.com",
        audience=client_id,
        role_claim=role_claim,
        admin_group=admin_group,
        operator_group=operator_group,
        viewer_group=viewer_group,
        jwks_cache_ttl=jwks_cache_ttl,
    )


def KeycloakConfig(
    base_url: str,
    realm: str,
    *,
    client_id: str = "meshflow-api",
    role_claim: str = "realm_access",
    admin_group: str = "meshflow-admins",
    operator_group: str = "meshflow-operators",
    viewer_group: str = "meshflow-viewers",
    jwks_cache_ttl: int = 3600,
) -> OIDCConfig:
    """Return an :class:`OIDCConfig` pre-configured for Keycloak.

    Parameters
    ----------
    base_url:
        Base URL of your Keycloak instance, e.g. ``https://keycloak.example.com``.
    realm:
        Keycloak realm name, e.g. ``myrealm``.
    client_id:
        Client ID configured in Keycloak (used as expected audience).
    role_claim:
        By default Keycloak puts realm roles under ``realm_access.roles``.
        The ``OIDCValidator`` will look in the top-level claim value; if you
        have a custom mapper exporting a flat ``roles`` array, pass ``roles``
        here instead.
    """
    base_url = base_url.rstrip("/")
    issuer = f"{base_url}/realms/{realm}"
    return OIDCConfig(
        issuer=issuer,
        audience=client_id,
        role_claim=role_claim,
        admin_group=admin_group,
        operator_group=operator_group,
        viewer_group=viewer_group,
        jwks_cache_ttl=jwks_cache_ttl,
    )
