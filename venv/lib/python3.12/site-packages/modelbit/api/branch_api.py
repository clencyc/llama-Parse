import logging

from modelbit.helpers import getCurrentBranch
from modelbit.utils import inDeployment
from modelbit.error import UserFacingError
from .api import MbApi

logger = logging.getLogger(__name__)


class BranchApi:
  api: MbApi

  def __init__(self, api: MbApi):
    self.api = api

  def raiseIfProtected(self) -> None:
    if not inDeployment():
      self.api.getJsonOrThrow("api/cli/v1/branch/check_protected", {"branch": getCurrentBranch()})

  def createBranch(self, branchName: str, baseName: str) -> None:
    if inDeployment():
      raise UserFacingError("Cannot create branches within deployments.")
    self.api.getJsonOrThrow("api/cli/v1/branch/create", {"name": branchName, "base": baseName})

  def branchExists(self, branchName: str) -> bool:
    resp = self.api.getJsonOrThrow("api/cli/v1/branch/exists", {"branch": branchName})
    return resp["exists"]
