import logging
import os
import subprocess
import sys
from typing import Optional, Tuple

from modelbit.api import CloneApi, CloneInfo
from modelbit.cli.ui import output
from modelbit.internal.auth import mbApi
from modelbit.internal.local_config import saveWorkspaceConfig
from .ssh_keys import canCheckForSshKeys, hasSshKeyLocally, userUploadKey
from modelbit.error import UserFacingError
from modelbit.telemetry import logEventToWeb
from modelbit.cli.deployment_create import countDeployments, createDeployment
from modelbit.ux import SHELL_FORMAT_FUNCS
from .repo_helpers import getRepoRoot

logger = logging.getLogger(__name__)

NeedSshKeyErrorMsg = "Upload an SSH key to clone from Modelbit. See https://doc.modelbit.com/git/getting-started-with-git"


def pickGitOrigin(cloneInfo: CloneInfo, origin: Optional[str]) -> Tuple[str, bool]:
  # Origin can be passed in via cmdline. If modelbit, use internal, otherwise use external
  if origin == "modelbit":
    return (cloneInfo.mbRepoUrl, True)
  elif origin is not None:
    if cloneInfo.forgeRepoUrl is None:
      output(f"You chose to clone from '{origin}' but that git remote is not configured in Modelbit.")
      exit(1)
    return (cloneInfo.forgeRepoUrl, False)

  return (cloneInfo.mbRepoUrl, True)


def doGitClone(workspaceId: str, gitUrl: str, targetDir: str) -> bool:
  cloneConfig = [
      "--config", "filter.modelbit.process=modelbit gitfilter process", "--config",
      "filter.modelbit.required", "--config", "merge.renormalize=true", "--depth=100", "--no-single-branch"
  ]

  env = dict(os.environ.items())
  env["MB_WORKSPACE_ID"] = workspaceId
  logger.info(f"Cloning {gitUrl} into {targetDir} for {workspaceId}")
  try:
    subprocess.run(["git", "clone", *cloneConfig, gitUrl, targetDir],
                   stdin=sys.stdin,
                   stdout=sys.stdout,
                   stderr=sys.stderr,
                   check=True,
                   env=env)
    return True
  except subprocess.CalledProcessError:
    output(
        "There was an error cloning your repository. Some large files may not have been restored. Please contact support."
    )
    return False


def isAcceptableDirName(targetDir: str) -> bool:
  for c in ["/", "\\", ";", ":", "@", "."]:
    if c in targetDir:
      return False
  return True


def isInsideProtectedDirectory(dir: str) -> bool:
  pathParts = os.path.normpath(dir).split(os.sep)
  return ".ssh" in pathParts


def isInsideGitRepo() -> bool:
  return getRepoRoot() is not None


def maybePrepFirstDeployment(targetDir: str) -> None:
  os.chdir(os.path.join(os.getcwd(), targetDir))
  numDeps = countDeployments(repoRoot=os.getcwd())
  if numDeps == 0:
    print("")
    print("--- --- ---")
    createDeployment(name="hello_world", needsCd=True)


def clone(targetDir: str = "modelbit", origin: Optional[str] = None) -> None:
  if targetDir and os.path.exists(targetDir):
    output(f"Error: Unable to clone repository. The target directory '{targetDir}' already exists.")
    exit(1)

  if not isAcceptableDirName(targetDir):
    output(f"Error: Unable to clone repository. The target directory '{targetDir}' is not a directory name.")
    exit(1)

  if isInsideProtectedDirectory(os.getcwd()):
    output(f"Error: Unable to clone repository. The current directory is protected.")
    exit(1)

  if isInsideGitRepo():
    output(f"Error: Unable to clone repository. The current directory is already a git repository.")
    exit(1)

  api = mbApi(source="clone")
  cloneInfo = CloneApi(api).getCloneInfo()
  if cloneInfo is None:
    raise Exception("Failed to authenticate. Please try again.")
  saveWorkspaceConfig(cloneInfo.workspaceId, cloneInfo.cluster, cloneInfo.gitUserAuthToken)

  gitUrl, _ = pickGitOrigin(cloneInfo, origin)
  if gitUrl == cloneInfo.mbRepoUrl:
    if canCheckForSshKeys():
      hasLocalKeys = hasSshKeyLocally(cloneInfo.sshKeys)
      logEventToWeb(api=api, name="Clone", details={
          "hasLocalKeys": hasLocalKeys,
      })
      if not hasLocalKeys and not userUploadKey(api=api):
        raise UserFacingError(NeedSshKeyErrorMsg)
    elif len(cloneInfo.sshKeys) == 0:
      raise UserFacingError(NeedSshKeyErrorMsg)
  if doGitClone(cloneInfo.workspaceId, gitUrl, targetDir):
    print("")
    print(SHELL_FORMAT_FUNCS["purple"]("Clone complete!"))
    maybePrepFirstDeployment(targetDir=targetDir)
