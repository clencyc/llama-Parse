from typing import Optional, Any, Dict, List, cast
from modelbit.api import MbApi
import logging

logger = logging.getLogger(__name__)


class SshKeyInfo:

  def __init__(self, data: Dict[str, Any]):
    self.sha256Fingerprint = data.get("sha256Fingerprint", "")


class CloneInfo:

  def __init__(self, data: Dict[str, Any]):
    self.workspaceId: str = data["workspaceId"]
    self.cluster: str = data["cluster"]
    self.gitUserAuthToken: str = data["gitUserAuthToken"]
    self.mbRepoUrl: str = data["mbRepoUrl"]
    self.forgeRepoUrl: Optional[str] = data.get("forgeRepoUrl", None)
    self.sshKeys: List[SshKeyInfo] = [SshKeyInfo(i) for i in cast(List[Any], (data.get("sshKeys") or []))]

  def __str__(self) -> str:
    return str(vars(self))


class CloneApi:
  api: MbApi

  def __init__(self, api: MbApi):
    self.api = api

  def getCloneInfo(self) -> Optional[CloneInfo]:
    resp = self.api.getJson("api/cli/v1/clone_info")
    if "errorCode" in resp:
      logger.info(f"Got response {resp}")
      return None
    if _isClusterRedirectResponse(resp):
      self.api.setUrls(resp["cluster"])
      return None
    return CloneInfo(resp)

  def uploadSshKey(self, keyNickname: str, keyData: str) -> None:
    self.api.getJsonOrThrow("api/cli/v1/auth/add_ssh_key", {"keyNickname": keyNickname, "keyData": keyData})


def _isClusterRedirectResponse(resp: Dict[str, Any]) -> bool:
  return "cluster" in resp and not "workspaceId" in resp
