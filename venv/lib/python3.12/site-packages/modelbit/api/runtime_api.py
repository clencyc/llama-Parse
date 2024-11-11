import logging
from typing import Any, Dict, List, Optional, Union
from modelbit.api import MbApi, writeLimiter

from .api import MbApi
from .common import OwnerInfo

logger = logging.getLogger(__name__)


class RuntimeDesc:

  def __init__(self, data: Dict[str, Any]):
    self.id: str = data["id"]
    self.name: str = data["name"]
    self.version: str = data["version"]
    self.deployedAtMs: int = data["deployedAtMs"]
    self.ownerInfo = OwnerInfo(data["ownerInfo"])


class DeployedRuntimeDesc:

  def __init__(self, data: Dict[str, Any]):
    self.name: str = data["name"]
    self.version: Optional[str] = data.get("version", None)
    self.branch: str = data["branch"]
    self.runtimeOverviewUrl: str = data["runtimeOverviewUrl"]
    self.message: str = data["message"]


class CopyRuntimeResult:

  def __init__(self, data: Dict[str, Any]):
    self.runtimeOverviewUrl: str = data.get("runtimeOverviewUrl", "")


class RestartRuntimeResult:

  def __init__(self, data: Dict[str, Any]):
    self.slotsRestartStatus: str = data.get("slotsRestartStatus", "restart-started")

  def started(self) -> bool:
    return self.slotsRestartStatus == "restart-started"

  def notRunning(self) -> bool:
    return self.slotsRestartStatus == "no-slots"

  def alreadyInProgress(self) -> bool:
    return self.slotsRestartStatus == "already-in-progress"


class RuntimeApi:
  api: MbApi

  def __init__(self, api: MbApi):
    self.api = api

  def listDeployments(self, branch: str) -> List[RuntimeDesc]:
    resp = self.api.getJsonOrThrow("api/cli/v1/runtimes/list", {"branch": branch})
    deployments = [RuntimeDesc(ds) for ds in resp.get("runtimes", [])]
    return deployments

  def createRuntime(self, branch: str, createRuntimeRequest: Dict[str, Any]) -> DeployedRuntimeDesc:
    resp = self.api.getJsonOrThrow("api/cli/v1/runtimes/create", {
        "branch": branch,
        "createRuntimeRequest": createRuntimeRequest
    })
    return DeployedRuntimeDesc(resp["runtime"])

  def updateRuntime(self, branch: str, runtimeName: str, dataFiles: Dict[str, str]) -> DeployedRuntimeDesc:
    resp = self.api.getJsonOrThrow("api/cli/v1/runtimes/update", {
        "branch": branch,
        "runtimeName": runtimeName,
        "dataFiles": dataFiles,
    })
    return DeployedRuntimeDesc(resp["runtime"])

  def copyRuntime(self, fromBranch: str, toBranch: str, runtimeName: str,
                  runtimeVersion: Union[str, int]) -> CopyRuntimeResult:
    resp = self.api.getJsonOrThrow(
        "api/cli/v1/runtimes/copy", {
            "fromBranch": fromBranch,
            "toBranch": toBranch,
            "runtimeName": runtimeName,
            "runtimeVersion": runtimeVersion,
        })
    return CopyRuntimeResult(resp)

  def restartSlots(self, branch: str, runtimeName: str, runtimeVersion: Union[str,
                                                                              int]) -> RestartRuntimeResult:
    writeLimiter.maybeDelay()
    resp = self.api.getJsonOrThrow("api/cli/v1/runtimes/slots/restart", {
        "branch": branch,
        "runtimeName": runtimeName,
        "runtimeVersion": runtimeVersion,
    })
    return RestartRuntimeResult(resp)

  def createTrainingJob(
      self,
      branch: str,
      createRuntimeRequest: Dict[str, Any],
  ) -> DeployedRuntimeDesc:
    resp = self.api.getJsonOrThrow("api/cli/v1/training_job/create", {
        "branch": branch,
        "createRuntimeRequest": createRuntimeRequest
    })
    return DeployedRuntimeDesc(resp["runtime"])
