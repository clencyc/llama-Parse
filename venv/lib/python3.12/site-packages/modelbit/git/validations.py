import ast
import logging
import os
import sys
import traceback
from io import StringIO
from os import path
from subprocess import check_output, run
from typing import Dict, List, Optional, cast

import yaml
import yaml.scanner
from modelbit.api import MbApi
from modelbit.api.clone_api import CloneApi
from modelbit.api.metadata_api import MetadataApi, MetadataValidationResponse
from modelbit.error import UserFacingError
from modelbit.internal.local_config import getWorkspaceConfig, saveWorkspaceConfig
from modelbit.telemetry import logEventToWeb
from modelbit.ux import SHELL_FORMAT_FUNCS

from .repo_helpers import getRepoRoot

logger = logging.getLogger(__name__)

MaxYamlLength: int = 250_000
MaxConfigSizeBytes: int = 250_000


def ensureGitRemoteAndCluster(api: MbApi, workspaceId: str) -> None:
  cloneApi = CloneApi(api)
  cloneInfo = cloneApi.getCloneInfo()
  if not cloneInfo:
    raise Exception("Could not reach modelbit servers. Please try again.")

  config = getWorkspaceConfig(workspaceId)
  if not config or config.cluster != cloneInfo.cluster:
    saveWorkspaceConfig(workspaceId, cloneInfo.cluster, cloneInfo.gitUserAuthToken)

  gitRemote = check_output(['git', 'remote', 'get-url', 'origin']).decode("utf8").rstrip()
  if gitRemote != cloneInfo.mbRepoUrl and gitRemote != cloneInfo.forgeRepoUrl:
    show(f"Updating git remote from {gitRemote} to {cloneInfo.mbRepoUrl}")
    check_output(['git', 'remote', 'set-url', 'origin', cloneInfo.mbRepoUrl])


def writePreCommitHook() -> None:
  try:
    repoRoot = getRepoRoot()
    if repoRoot is None:
      raise Exception(f"Could not find repository near {os.getcwd()}")
    hookPath = os.path.join(repoRoot, ".git/hooks/pre-commit")
    with open(hookPath, "w") as f:
      f.write("""#!/bin/sh

exec modelbit validate
""")
    os.chmod(hookPath, 0o775)
  except Exception as err:
    print(f"Unable to write pre-commit hook: {err}", file=sys.stderr)


def show(message: str) -> None:
  print(message, file=sys.stderr)


def _red(message: str) -> str:
  return SHELL_FORMAT_FUNCS["red"](message)


def _purple(message: str) -> str:
  return SHELL_FORMAT_FUNCS["purple"](message)


def _linkBlue(message: str) -> str:
  return SHELL_FORMAT_FUNCS["link"](message)


def validateRepo(mbApi: MbApi, dir: str) -> bool:
  try:
    vDeps = validateDeployments(mbApi, dir)
    vJobs = validateTrainingJobs(mbApi, dir)
    vRepo = validateRepoShape(dir)
    return vDeps and vJobs and vRepo
  except UserFacingError as err:  # intentional errors to block shipping
    show(_red("Validation error: ") + str(err))
    return False
  except Exception as err:  # bugs that'll block deployments so let them through
    show(_red("Unexpected error: ") + str(err))
    show(traceback.format_exc())
    return True


def validateDeployments(mbApi: MbApi, dir: str) -> bool:
  depsPath = path.join(dir, "deployments")
  validationsPassed: bool = True

  if not path.exists(depsPath):
    raise ValueError(f"Unable to read deployments directory '{depsPath}'")

  allDeploymentNames = sorted([d for d in os.listdir(depsPath) if path.isdir(path.join(depsPath, d))])
  if len(allDeploymentNames) == 0:
    return validationsPassed

  show("\nValidating deployments...")
  metadataValidations = validateMetadataFiles(mbApi, depsPath)
  failed: List[str] = []
  for d in allDeploymentNames:
    passed = validateOneDeployment(depsPath,
                                   d,
                                   metadataValidationError=metadataValidations.getError(
                                       path.join(depsPath, d, "metadata.yaml")))
    if not passed:
      failed.append(d)
    validationsPassed = validationsPassed and passed
  show("")
  logEventToWeb(api=mbApi,
                name="ValidateDeployments",
                details={
                    "failedNames": ",".join(failed),
                    "numFailed": len(failed),
                    "total": len(allDeploymentNames)
                })
  return validationsPassed


def validateOneDeployment(depsPath: str, depName: str, metadataValidationError: Optional[str]) -> bool:

  def pathExists(fileName: str) -> bool:
    return path.exists(path.join(depsPath, depName, fileName))

  if pathExists(".archived"):
    return True  # don't validate archived deployments

  mainFuncError: Optional[str] = getMainFuncError(
      path.join(depsPath, depName),
      helpUrl="https://doc.modelbit.com/api-reference/files/deployment-metadata-yaml/")

  if not pathExists("source.py"):
    show(f"  ❌ {_red(depName)}: Missing {_purple('source.py')}")
    return False
  elif not pathExists("metadata.yaml"):
    show(f"  ❌ {_red(depName)}: Missing {_purple('metadata.yaml')}")
    return False
  elif metadataValidationError:
    show(f"  ❌ {_red(depName)}: Error in {_purple('metadata.yaml')}:" + f" {metadataValidationError}")
    return False
  elif mainFuncError:
    show(f"  ❌ {_red(depName)}: Error in {_purple('metadata.yaml')}:" + f" {mainFuncError}")
    return False
  else:
    show(f"  ✅ {depName}")
    return True


def validateTrainingJobs(mbApi: MbApi, dir: str) -> bool:
  jobsPath = path.join(dir, "training_jobs")
  validationsPassed: bool = True

  if not path.exists(jobsPath):
    return validationsPassed

  allJobNames = sorted([j for j in os.listdir(jobsPath) if path.isdir(path.join(jobsPath, j))])
  if len(allJobNames) == 0:
    return validationsPassed

  show("\nValidating training jobs...")
  metadataValidations = validateJobMetadataFiles(mbApi, jobsPath)
  failed: List[str] = []
  for j in allJobNames:
    passed = validateOneTrainingJob(jobsPath,
                                    j,
                                    metadataValidationError=metadataValidations.getError(
                                        path.join(jobsPath, j, "metadata.yaml")))
    if not passed:
      failed.append(j)
    validationsPassed = validationsPassed and passed
  show("")
  logEventToWeb(api=mbApi,
                name="ValidateTrainingJobs",
                details={
                    "failedNames": ",".join(failed),
                    "numFailed": len(failed),
                    "total": len(allJobNames)
                })
  return validationsPassed


def validateOneTrainingJob(jobsPath: str, jobName: str, metadataValidationError: Optional[str]) -> bool:

  def pathExists(fileName: str) -> bool:
    return path.exists(path.join(jobsPath, jobName, fileName))

  mainFuncError: Optional[str] = getMainFuncError(
      path.join(jobsPath, jobName),
      helpUrl="https://doc.modelbit.com/api-reference/files/training-metadata-yaml/")

  if not pathExists("metadata.yaml"):
    show(f"  ❌ {_red(jobName)}: Missing {_purple('metadata.yaml')}")
    return False
  elif metadataValidationError:
    show(f"  ❌ {_red(jobName)}: Error in {_purple('metadata.yaml')}:" + f" {metadataValidationError}")
    return False
  elif mainFuncError:
    show(f"  ❌ {_red(jobName)}: Error in {_purple('metadata.yaml')}:" + f" {mainFuncError}")
    return False
  else:
    show(f"  ✅ {jobName}")
    return True


# returns filePath -> fileContents. Getting them all so they can be in one web request
def collectMetadataFiles(depsPath: str) -> Dict[str, str]:
  files: Dict[str, str] = {}
  for d in os.listdir(depsPath):
    filePath = path.join(depsPath, d, "metadata.yaml")
    if path.exists(filePath):
      with open(filePath) as f:
        files[filePath] = f.read()
  return files


# returns path -> Optional[errorMessage]
def validateMetadataFiles(mbApi: MbApi, depsPath: str) -> MetadataValidationResponse:
  files = collectMetadataFiles(depsPath)
  return MetadataApi(mbApi).validateMetadataFiles(files)


def validateJobMetadataFiles(mbApi: MbApi, jobsPath: str) -> MetadataValidationResponse:
  files = collectMetadataFiles(jobsPath)
  return MetadataApi(mbApi).validateJobMetadataFiles(files)


def getMainFuncError(depPath: str, helpUrl: str) -> Optional[str]:
  helpLink = _linkBlue(helpUrl)

  try:
    metadataPath = path.join(depPath, "metadata.yaml")
    if not os.path.exists(metadataPath):
      return None
    with open(metadataPath) as f:
      rawYaml = f.read()

    if len(rawYaml) > MaxYamlLength:
      return f"The file is too large. Max size is  {int(MaxYamlLength/1024)}k. File is {int(len(rawYaml)/1024)}k."

    obj = yaml.safe_load(StringIO(rawYaml))
    mainFunc = obj["runtimeInfo"].get("mainFunction")
    mainFuncArgs = obj["runtimeInfo"].get("mainFunctionArgs")

    with open(path.join(depPath, "source.py")) as f:
      sourceCode = f.read()
      if mainFunc is None:
        return f"The mainFunction parameter is missing from metadata.yaml. See {helpLink}"
      if type(mainFunc) is not str:
        return f"The mainFunction parameter must be a string. See {helpLink}"
      if type(mainFuncArgs) is not list and mainFuncArgs is not None:
        return f"The mainFunctionArgs parameter must be a list of strings. See {helpLink}"
      if mainFunc not in sourceCode:
        return f"The mainFunction '{mainFunc}' was not found in source.py"

      mainArgsErr = getMainFunctionArgsError(source=sourceCode,
                                             mainFunctionName=mainFunc,
                                             expectedArgs=cast(Optional[List[str]], mainFuncArgs),
                                             helpLink=helpLink)
      if mainArgsErr is not None:
        return mainArgsErr
  except yaml.scanner.ScannerError as err:
    return err.problem
  except:
    pass  # if something goes wrong in the parsing other validators would have caught it

  return None


def getMainFunctionArgsError(source: str, mainFunctionName: str, expectedArgs: Optional[List[str]],
                             helpLink: str) -> Optional[str]:

  parsedArgNames = parseMainFunctionArgs(source=source, mainFunctionName=mainFunctionName)
  if parsedArgNames is None:
    return None
  if len(parsedArgNames) != 0 and (expectedArgs is None or len(expectedArgs) == 0):
    return "The mainFunctionArgs parameter is missing. See {helpLink}"
  if expectedArgs is None:
    return None
  for a in expectedArgs:
    if type(a) is not str:
      return f"The mainFunctionArgs parameter should be a list of strings. Make sure there isn't a space after the ':' to match the format of 'arg_name:arg_type'. See {helpLink}"

  expectedArgNames = [a.split(":")[0] for a in expectedArgs]
  for eName in expectedArgNames:
    if eName == "return":
      continue
    if eName not in parsedArgNames:
      return f"Unexpected mainFunctionArgs argument named '{eName}'. It wasn't found in function '{mainFunctionName}'"
  for pName in parsedArgNames:
    if pName not in expectedArgNames:
      return f"The argument '{pName}' of function '{mainFunctionName}' is missing from mainFunctionArgs"

  return None


def parseMainFunctionArgs(source: str, mainFunctionName: str) -> Optional[List[str]]:
  try:
    for a in ast.parse(source).body:
      if type(a) is ast.FunctionDef:
        if a.name == mainFunctionName:
          return [a.arg for a in a.args.args]
    return None
  except Exception as err:
    logger.error("ErrorParsingSource.py", exc_info=err)
    return None  # other tests should have caught this, so we can drop the error


def isGitIgnored(repoRoot: str, pathName: str) -> bool:
  checkPath = pathName
  if path.isdir(path.join(repoRoot, pathName)) and not pathName.endswith(path.sep):
    checkPath = pathName + path.sep
  return run(["git", "check-ignore", "-q", checkPath], cwd=repoRoot).returncode == 0


def isSmallConfigFile(repoRoot: str, pathName: str) -> bool:
  fullPath = path.join(repoRoot, pathName)
  if path.isdir(fullPath):
    return isSmallConfigDir(repoRoot=repoRoot, pathName=pathName)
  if not path.basename(fullPath).startswith("."):
    return False
  return path.getsize(fullPath) < MaxConfigSizeBytes


def isSmallConfigDir(repoRoot: str, pathName: str) -> bool:
  fullPath = path.join(repoRoot, pathName)
  if not path.isdir(fullPath):
    return False
  if not path.basename(fullPath).startswith("."):
    return False
  for fName in os.listdir(fullPath):
    childPath = path.join(fullPath, fName)
    if path.isdir(childPath):
      return False
    if path.getsize(childPath) > MaxConfigSizeBytes:
      return False
  return True


def isAllowedFile(repoRoot: str, pathName: str) -> bool:
  allowedInRoot = [
      ".git",
      ".gitignore",
      ".gitattributes",
      ".workspace",
      "bin",
      "README.md",
      "README",
      "README.txt",
      "deployments",
      "datasets",
      "endpoints",
      "notebooks",
      "common",
      "registry",
      "packages",
      ".mbtest",
      "LICENSE",
      "LICENSE.md",
      "LICENSE.txt",
      "training_jobs",
  ]
  return (pathName in allowedInRoot or isGitIgnored(repoRoot=repoRoot, pathName=pathName) or
          isSmallConfigFile(repoRoot=repoRoot, pathName=pathName) or
          isSmallConfigDir(repoRoot=repoRoot, pathName=pathName))


def validateRepoShape(repoRoot: str) -> bool:

  disallowed = [f for f in os.listdir(repoRoot) if not isAllowedFile(repoRoot, f)]

  if len(disallowed) == 0:
    return True

  show("\nValidating repository...")
  show(
      f"  ❌ Extra files and directories are not allowed in the root of the Modelbit repository. Please remove: "
      + ", ".join(disallowed))
  show("")
  return False
