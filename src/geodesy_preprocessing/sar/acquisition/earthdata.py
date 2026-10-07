"""SAR 获取工作流共用的 Earthdata 安全认证入口。

功能概述:
    按令牌、成对环境变量、``.netrc`` 的明确优先级建立 ASF 会话；认证失败时不把
    账号、密码或令牌带入异常文本。

函数说明:
    ``earthdata_session_from_environment`` 从运行环境解析凭据并返回认证会话。
"""

from __future__ import annotations

import netrc
import os
from collections.abc import Callable
from pathlib import Path


def earthdata_session_from_environment(
    *,
    netrc_path: str | Path | None = None,
    session_factory: Callable[[], object] | None = None,
):
    """Build an authenticated ASF session without exposing credential values."""

    token = os.environ.get("EARTHDATA_TOKEN")
    username = os.environ.get("EARTHDATA_USERNAME")
    password = os.environ.get("EARTHDATA_PASSWORD")
    if token and (username or password):
        raise ValueError("choose EARTHDATA_TOKEN or EARTHDATA_USERNAME/PASSWORD, not both")
    if bool(username) != bool(password):
        raise ValueError("EARTHDATA_USERNAME and EARTHDATA_PASSWORD must be set together")
    if not token and not username:
        source = Path(netrc_path).expanduser() if netrc_path is not None else Path.home() / ".netrc"
        try:
            authentication = netrc.netrc(str(source)).authenticators("urs.earthdata.nasa.gov")
        except (FileNotFoundError, netrc.NetrcParseError, OSError):
            authentication = None
        if authentication is not None:
            username, _, password = authentication
    if not token and not (username and password):
        raise ValueError(
            "Earthdata credentials are missing; set EARTHDATA_TOKEN, paired "
            "EARTHDATA_USERNAME/PASSWORD, or ~/.netrc"
        )
    if session_factory is None:
        import asf_search as asf

        session_factory = asf.ASFSession
    session = session_factory()
    try:
        if token:
            return session.auth_with_token(token)
        return session.auth_with_creds(username, password)
    except Exception:
        raise RuntimeError("Earthdata authentication failed") from None


__all__ = ["earthdata_session_from_environment"]
