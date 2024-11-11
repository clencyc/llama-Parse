import base64
import logging
from typing import Any, Dict, Optional, List

from .api import MbApi, writeLimiter, readLimiter

logger = logging.getLogger(__name__)


class SecretDesc:
  secretValue: Optional[bytes] = None

  def __init__(self, data: Dict[str, Any]):
    if "secretValue64" in data:
      self.secretValue = base64.b64decode(data["secretValue64"])

  def __repr__(self) -> str:
    return str(self.__dict__)


class SecretApi:
  api: MbApi

  def __init__(self, api: MbApi):
    self.api = api

  def getSecret(self, branch: str, secretName: str, runtimeName: str) -> Optional[SecretDesc]:
    readLimiter.maybeDelay()
    resp = self.api.getJsonOrThrow("api/cli/v1/secrets/get",
                                   dict(secretName=secretName, runtimeName=runtimeName, branch=branch))
    return SecretDesc(resp['secretInfo']) if 'secretInfo' in resp else None

  def setSecret(self, name: str, secretValue: str, runtimeNameFilter: str, runtimeBranchFilter: str) -> None:
    writeLimiter.maybeDelay()
    self.api.getJsonOrThrow(
        "api/cli/v1/secrets/set",
        dict(name=name,
             secretValue=secretValue,
             runtimeNameFilter=runtimeNameFilter,
             runtimeBranchFilter=runtimeBranchFilter))

  def listIntegrationEnvVars(self) -> List[str]:
    resp = self.api.getJsonOrThrow("api/cli/v1/secrets/list_integrations")
    return resp['keys'] if 'keys' in resp else []
