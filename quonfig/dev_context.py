"""Dev-only context loader.

Reads the per-domain tokens file written by ``qfg login`` (e.g.
``~/.quonfig/tokens.json`` for production, ``tokens-quonfig-staging-com.json``
for staging) and returns ``{"quonfig-user": {"email": ...}}`` when a
userEmail is present. Returns ``None`` when the file is missing, unreadable,
or has no userEmail.

The attribute is dev-only by construction: production servers do not run
``qfg login`` and therefore have no tokens file. Rules keyed on
``quonfig-user.email`` are dead code in prod.

Mirrors sdk-node ``src/devContext.ts`` and sdk-go ``dev_context.go``.
``QUONFIG_CONFIG_HOME`` overrides the parent of the ``.quonfig`` directory
for test isolation; defaults to ``Path.home()``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from .types import Contexts

logger = logging.getLogger(__name__)


def token_filename_for_api_urls(api_urls: Optional[List[str]] = None) -> str:
    """Pick the per-domain tokens file written by ``qfg login``.

    Mirrors ``cli/src/util/token-storage.ts``: the CLI writes plain
    ``tokens.json`` only when the domain is ``quonfig.com``; any other
    domain (e.g. ``quonfig-staging.com``) is suffixed with the
    dash-replaced domain. The SDK derives the domain from the first
    configured API URL by stripping a leading ``app.`` or ``primary.``
    subdomain. An empty list, an unparseable URL, or a host that
    resolves to ``quonfig.com`` falls back to ``tokens.json``.
    """
    domain = _derive_domain_from_api_urls(api_urls)
    if not domain or domain == "quonfig.com":
        return "tokens.json"
    return f"tokens-{domain.replace('.', '-')}.json"


def _derive_domain_from_api_urls(api_urls: Optional[List[str]]) -> str:
    if not api_urls or not api_urls[0]:
        return ""
    try:
        host = urlparse(api_urls[0]).hostname or ""
    except ValueError:
        return ""
    for prefix in ("app.", "primary."):
        if host.startswith(prefix):
            return host[len(prefix) :]
    return host


def _config_home() -> Path:
    """The parent of ``.quonfig``. ``QUONFIG_CONFIG_HOME`` overrides for
    test isolation; defaults to the user's home directory."""
    override = os.environ.get("QUONFIG_CONFIG_HOME", "").strip()
    if override:
        return Path(override)
    return Path.home()


def load_quonfig_user_context(
    api_urls: Optional[List[str]] = None,
) -> Optional[Contexts]:
    """Read the tokens file and return ``{"quonfig-user": {"email": ...}}``
    or ``None`` when the file is missing, has no userEmail, or cannot be
    parsed. Parse failures emit a single ``logger.warning`` and yield
    ``None`` so SDK init can continue.
    """
    path = _config_home() / ".quonfig" / token_filename_for_api_urls(api_urls)

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        logger.warning("dev-context: could not read %s (%s); skipping injection", path, e)
        return None

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("dev-context: could not parse %s (%s); skipping injection", path, e)
        return None

    if not isinstance(parsed, dict):
        return None
    email = parsed.get("userEmail")
    if not isinstance(email, str) or not email:
        return None

    return {"quonfig-user": {"email": email}}
