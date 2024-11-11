import os
import re
import sys
from typing import List

from modelbit.git.repo_helpers import getRepoRoot
from modelbit.ux import SHELL_FORMAT_FUNCS
from modelbit.error import UserFacingError
from modelbit.internal.auth import mbApi
from modelbit.telemetry import logEventToWeb
from modelbit.api import MbApi


def countDeployments(repoRoot: str) -> int:
  depPath = os.path.join(repoRoot, "deployments")
  if not os.path.exists(depPath):
    return 0
  return len([i for i in os.listdir(depPath) if os.path.isdir(os.path.join(depPath, i))])


def createDeployment(name: str, needsCd: bool = False) -> None:
  repoRoot = getRepoRoot()

  if repoRoot is None or not os.path.exists(os.path.join(repoRoot, ".workspace")):
    raise UserFacingError("The current directory does not appear to be a Modelbit repository.")

  _assertDeploymentName(name=name, repoRoot=repoRoot)
  depPath = os.path.join(repoRoot, "deployments", name)
  api = mbApi()

  writeTextFile(os.path.join(depPath, "source.py"), generateSourcePy(name))
  writeTextFile(os.path.join(depPath, "metadata.yaml"),
                generateMetadataYaml(name=name, userEmail=_getUserEmail(api)))
  writeTextFile(os.path.join(depPath, "requirements.txt"), "")

  print("")
  print(f"Deployment '{SHELL_FORMAT_FUNCS['purple'](name)}' has been created!")
  print("")
  sourcePath = os.path.join(os.path.relpath(repoRoot, os.path.curdir), "deployments", name, "source.py")
  gitPushCommandParts: List[str] = []
  if needsCd:
    gitPushCommandParts.append(f"cd {os.path.basename(repoRoot)}")
  gitPushCommandParts += ["git add .", f'git commit -m "Created {name}"', "git push"]
  gitPushCommand = SHELL_FORMAT_FUNCS['green'](" && ".join(gitPushCommandParts))
  numDeployments = countDeployments(repoRoot)
  if numDeployments == 1:  # FTUE!
    print(f"The source code is in '{SHELL_FORMAT_FUNCS['purple'](sourcePath)}'")
    print("")
    print(f"  Commit and push your deployment to Modelbit:\n  {gitPushCommand}")
  else:
    print(f"  Make changes, commit, and then deploy to Modelbit:\n  {gitPushCommand}")
  print("")
  logEventToWeb(api=api, name="CreateDeployment", details={"name": name, "countLocal": numDeployments})


def generateSourcePy(name: str) -> str:
  returnVal = "Hello world! Input is: {input}"
  return f"""
import modelbit, sys

# main function
def {name}(input: str) -> str:
  return f"{returnVal}"

# to run locally run: python3 source.py 42
if __name__ == '__main__':
   print({name}(*sys.argv[1:]))
  """.strip() + "\n"


def generateMetadataYaml(name: str, userEmail: str) -> str:
  return f"""
owner: {userEmail}
runtimeInfo:
  mainFunction: {name}
  mainFunctionArgs:
    - input:str
  pythonVersion: '{_currentPythonVersion()}'
schemaVersion: 2
  """.strip() + "\n"


def _assertDeploymentName(name: str, repoRoot: str) -> None:
  if not re.match('^[a-zA-Z0-9_]+$', name):
    raise UserFacingError("Names should be alphanumeric with underscores.")
  if os.path.exists(os.path.join(repoRoot, "deployments", name)):
    raise UserFacingError(f"A deployment named '{name}' already exists.")


def _currentPythonVersion() -> str:
  return f"{sys.version_info.major}.{sys.version_info.minor}"


def _getUserEmail(api: MbApi) -> str:
  if api.loginState and api.loginState.userEmail:
    return api.loginState.userEmail
  return ""


def writeTextFile(path: str, contents: str) -> None:
  os.makedirs(os.path.dirname(path), exist_ok=True)
  with open(path, "w") as f:
    f.write(contents)
