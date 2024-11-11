from modelbit.api import BranchApi, MbApi
from modelbit.utils import inDeployment
from modelbit.ux import printTemplate
from modelbit.error import UserFacingError
from modelbit.helpers import getCurrentBranch
from typing import Optional, Any
import re


def assertIsValidBranchName(name: Any) -> None:
  if type(name) is not str or len(name) == 0:
    raise UserFacingError("Branch names must be strings.")
  elif name != name.lower():
    raise UserFacingError("Branch names must be lowercase.")
  elif not re.match('^[a-z][a-z0-9/_-]*$', name):  # see gitCreateBranchPattern
    raise UserFacingError(
        "Branch names can only container letters, numbers, underscores, hyphens and slashes. Names must start with a letter."
    )


def createBranch(mbApi: MbApi, branchName: str, baseName: Optional[str] = None) -> None:
  assertIsValidBranchName(branchName)
  if baseName is None:
    baseName = getCurrentBranch()
  if inDeployment():
    raise UserFacingError("Cannot create branches within deployments.")
  BranchApi(mbApi).createBranch(branchName=branchName, baseName=baseName)
  printTemplate("message", None, msgText=f"Created branch '{branchName}' from '{baseName}'.")


def checkBranchExists(mbApi: MbApi, branchName: str) -> bool:
  if inDeployment():
    return True
  return BranchApi(mbApi).branchExists(branchName=branchName)
