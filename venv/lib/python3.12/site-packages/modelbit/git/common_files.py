from typing import Optional, List
from .repo_helpers import getRepoRoot
from modelbit.error import UserFacingError
from modelbit.ux import SHELL_FORMAT_FUNCS
import os, shutil
from fnmatch import fnmatch
from os import path
from pathlib import Path

DepDirName = "deployments"
JobDirName = "training_jobs"
ComDirName = "common"


def getCurrentDepName(repoRoot: str) -> Optional[str]:
  return getCurrentJobOrDepName(repoRoot, DepDirName)


def getCurrentJobName(repoRoot: str) -> Optional[str]:
  return getCurrentJobOrDepName(repoRoot, JobDirName)


def getCurrentJobOrDepName(repoRoot: str, baseDirName: str) -> Optional[str]:
  cwd = os.getcwd()
  if not cwd.startswith(repoRoot):
    raise UserFacingError(f"Expecting {cwd} to be under {repoRoot}")

  current = Path(cwd).name
  for parent in Path(cwd).parents:
    if path.basename(parent) == baseDirName:
      return current
    current = parent.name
  return None


def fullPathToDeployment(repoRoot: str, depName: str) -> str:
  return fullPathToJobOrDeployment(repoRoot, depName, DepDirName)


def fullPathToJob(repoRoot: str, jobName: str) -> str:
  return fullPathToJobOrDeployment(repoRoot, jobName, JobDirName)


def fullPathToJobOrDeployment(repoRoot: str, jobOrDepName: str, baseDirName: str) -> str:
  jobOrDepPath = path.join(repoRoot, baseDirName, jobOrDepName)
  if not path.exists(jobOrDepPath):
    raise UserFacingError(f"Deployment {jobOrDepName} not found under {jobOrDepPath}")
  return jobOrDepPath


def enumerateCommonFiles(repoRoot: str, pattern: Optional[str], relCommonPath: str = "") -> List[str]:
  common: List[str] = []
  searchRoot = path.join(repoRoot, "common", relCommonPath)
  for name in os.listdir(searchRoot):
    if name.startswith(".") or name == "settings.yaml":
      continue
    relName = path.join(relCommonPath, name)
    matches = fnmatch(relName, pattern or "*")
    fullPath = path.join(searchRoot, name)
    if path.islink(fullPath):
      continue  # ignore links within common to avoid loops
    elif matches:
      common.append(relName)
    elif path.isdir(fullPath):
      common += enumerateCommonFiles(
          repoRoot=repoRoot,
          pattern=pattern,
          relCommonPath=path.join(relCommonPath, name),
      )
  return sorted(common)


def addSymlinks(jobOrDepPath: str, baseDirName: str, repoRoot: str, common: List[str]) -> None:
  for c in common:

    pathInJobOrDep = path.join(jobOrDepPath, c)
    os.makedirs(path.dirname(pathInJobOrDep), exist_ok=True)

    pathInCommon = path.join(repoRoot, ComDirName, c)

    if path.exists(pathInJobOrDep):
      if path.isdir(pathInJobOrDep) and not path.islink(pathInJobOrDep):
        shutil.rmtree(pathInJobOrDep)
      else:
        os.unlink(pathInJobOrDep)

    relLinkToCommon = path.relpath(pathInCommon, path.dirname(pathInJobOrDep))
    relLinkInJobOrDep = path.relpath(pathInJobOrDep, path.join(repoRoot, baseDirName))
    purple = SHELL_FORMAT_FUNCS['purple']
    print(f"{purple('Linking')} {relLinkInJobOrDep} {purple('-->')} {path.join(ComDirName, c)}")
    os.symlink(relLinkToCommon, pathInJobOrDep)


def linkCommonFiles(depName: Optional[str] = None,
                    jobName: Optional[str] = None,
                    pattern: Optional[str] = None) -> None:
  repoRoot = getRepoRoot()
  if repoRoot is None:
    raise UserFacingError(f"Could not find repository near {os.getcwd()}")

  cFilePaths = enumerateCommonFiles(repoRoot=repoRoot, pattern=pattern)
  if len(cFilePaths) == 0:
    raise UserFacingError(f"No common files matched the pattern {pattern}")

  depName = depName or getCurrentDepName(repoRoot)
  jobName = jobName or getCurrentJobName(repoRoot)
  if not depName and not jobName:
    cwd = os.getcwd()
    raise UserFacingError(
        f"Could not find current deployment or training job in {cwd}. Specify it with --deployment or --job")

  if depName:
    depPath = fullPathToDeployment(repoRoot, depName)
    addSymlinks(depPath, baseDirName=DepDirName, repoRoot=repoRoot, common=cFilePaths)
  if jobName:
    jobPath = fullPathToJob(repoRoot, jobName)
    addSymlinks(jobPath, baseDirName=JobDirName, repoRoot=repoRoot, common=cFilePaths)
