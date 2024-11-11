import logging
import os
from threading import Thread
from time import sleep
from typing import Optional, cast, Dict, List
import json
import re

from modelbit.api import MbApi
from modelbit.api.api import NotebookEnv
from modelbit.error import UserFacingError
from modelbit.helpers import getCurrentBranch, pkgVersion, setCurrentBranch
from modelbit.utils import inDeployment, inModelbitCI, inNotebook, inRuntimeJob
from modelbit.ux import COLORS, makeCssStyle, printTemplate

logger = logging.getLogger(__name__)

__mbApi: MbApi = MbApi()


def mbApi(region: Optional[str] = None, source: Optional[str] = None, branch: Optional[str] = None) -> MbApi:
  assertValidRegion(region)
  global __mbApi
  if not isAuthenticated():
    _performLogin(__mbApi, region=region, source=source, branch=branch)
  elif branch is not None:
    setCurrentBranch(branch)
    _printAuthenticatedMessage(__mbApi)
  return __mbApi


def mbApiReadOnly() -> MbApi:
  global __mbApi
  return __mbApi


def isAuthenticated() -> bool:
  global __mbApi
  if inRuntimeJob():
    _maybeAuthenticateJob(__mbApi)
    return True
  if inDeployment():
    return True
  elif __mbApi.isAuthenticated():
    return True
  else:
    __mbApi.loginState = None
    __mbApi.authToken = None
    return False


def apiKeyFromEnv() -> Optional[str]:
  return os.getenv("MB_API_KEY")


def workspaceNameFromEnv() -> Optional[str]:
  return os.getenv("MB_WORKSPACE_NAME")


def maybeWarnAboutVersion() -> None:
  needsUpgrade = _pipUpgradeInfo(__mbApi.mostRecentVersion)
  if needsUpgrade is None:
    return
  printTemplate("pip-upgrade", None, needsUpgrade=needsUpgrade)


###


def assertValidRegion(region: Optional[str]) -> None:
  if region is None or region in [
      "app", "us-east-1", "us-east-2.aws", "us-east-1.aws", "ap-south-1", "localhost", "web"
  ]:
    return
  raise UserFacingError(f"The region '{region}' is invalid.")


def _maybeAuthenticateJob(api: MbApi) -> None:
  if inRuntimeJob():
    nbEnvJsonStr = os.getenv("NOTEBOOK_ENV_JSON")
    if nbEnvJsonStr is not None:
      api.setLoginState(NotebookEnv(json.loads(nbEnvJsonStr)))


def _performLogin(api: MbApi,
                  region: Optional[str] = None,
                  source: Optional[str] = None,
                  branch: Optional[str] = None) -> None:
  setCurrentBranch(branch or _determineCurrentBranch())
  if api.isAuthenticated():
    return
  elif inRuntimeJob():
    _maybeAuthenticateJob(api)
  elif inDeployment():
    return
  elif apiKeyFromEnv() is not None:
    _performApiKeyLogin(api, region)
  elif inNotebook() or inModelbitCI():
    _performBrowserLogin(api, region, source=source)
  else:
    if not _performCLILogin(api):
      _performBrowserLogin(api, region, waitForResponse=True, source=source)


def _performCLILogin(api: MbApi) -> bool:
  from modelbit.git.workspace import findWorkspace
  from modelbit.internal.local_config import getWorkspaceConfig
  try:
    config = getWorkspaceConfig(findWorkspace())
    if not config:
      raise KeyError("Workspace credentials not found")
  except KeyError:
    return False
  api.setUrls(config.cluster)
  api.setToken(config.gitUserAuthToken.replace("mbpat-", ""))
  if not api.refreshAuthentication(getCurrentBranch()):
    return False
  _printAuthenticatedMessage(api)
  return True


def assertProperApiKey(apiKey: Optional[str]) -> None:
  if apiKey is None or type(apiKey) is not str or apiKey.strip() != apiKey or ":" not in apiKey:
    raise UserFacingError(
        "Invalid API key. Check the value of the envvar MB_API_KEY. API keys look like 'mi...:ms...'")


def assertProperWorkspaceName(workspaceName: Optional[str]) -> None:

  if workspaceName is None or type(workspaceName) is not str or workspaceName.strip(
  ) != workspaceName or workspaceName == "" or re.search(r"[^a-z0-9-]", workspaceName):
    errorMsg = "Invalid workspace name. Check the value of the envvar MB_WORKSPACE_NAME. Workspace names are made of lowercase letters and numbers."
    if type(workspaceName) is str:
      tryNameMatch = re.search('/w/(([a-z0-9]-?)*[a-z0-9])/?', workspaceName)  # workspaceNameSchema
      if tryNameMatch:
        errorMsg = f"{errorMsg} Try setting MB_WORKSPACE_NAME to '{tryNameMatch[1]}'"
    raise UserFacingError(errorMsg)


def _performApiKeyLogin(api: MbApi, region: Optional[str]) -> None:
  apiKey = apiKeyFromEnv()
  workspaceName = workspaceNameFromEnv()

  assertProperApiKey(apiKey)
  apiKey = cast(str, apiKey)

  assertProperWorkspaceName(workspaceName)
  workspaceName = cast(str, workspaceName)

  apiKeyId = apiKey.split(':')[0]
  logger.info(
      f"Attempting to log in with API Key {apiKeyId} to workspace {workspaceName} branch {getCurrentBranch()}."
  )
  if region is not None:
    api.setUrls(f"{region}.modelbit.com")
  source = "notebook" if inNotebook() else "terminal"
  nbEnv = api.loginWithApiKey(apiKey, workspaceName, source)
  if nbEnv is None:
    raise UserFacingError(f"Failed to log in with API Key {apiKeyId} to workspace {workspaceName}.")
  _printAuthenticatedMessage(api)


def _performBrowserLogin(api: MbApi,
                         region: Optional[str] = None,
                         waitForResponse: bool = False,
                         source: Optional[str] = None) -> None:
  if region is not None:
    api.setUrls(f"{region}.modelbit.com")
  api.refreshAuthentication(getCurrentBranch())
  displayId = "mbLogin"
  _printLoginMessage(api, source, displayId)

  def _pollForLoggedIn() -> None:
    triesLeft = 150
    while not api.isAuthenticated() and triesLeft > 0:
      triesLeft -= 1
      sleep(3)
      api.refreshAuthentication(getCurrentBranch())
    if api.isAuthenticated():
      _printAuthenticatedMessage(api, displayId)
    else:
      printTemplate("login-timeout", displayId)

  if waitForResponse:
    _pollForLoggedIn()
  else:
    loginThread = Thread(target=_pollForLoggedIn)
    if not inModelbitCI():
      loginThread.start()


def _determineCurrentBranch() -> str:
  from modelbit.git.workspace import findCurrentBranch
  return os.getenv("BRANCH", findCurrentBranch() or "main")


def _printAuthenticatedMessage(api: MbApi, displayId: Optional[str] = None) -> None:
  inRegion: Optional[str] = None
  if api.getCluster() != api.DEFAULT_CLUSTER:
    inRegion = api.getRegion()
  loginState = api.loginState
  if loginState is None:
    return
  styles = {
      "connected": makeCssStyle({
          "color": COLORS["success"],
          "font-weight": "bold",
      }),
      "info": makeCssStyle({
          "font-family": "monospace",
          "font-weight": "bold",
          "color": COLORS["brand"],
      })
  }
  printTemplate("authenticated",
                displayId,
                updateDisplayId=True,
                styles=styles,
                email=loginState.userEmail,
                workspace=loginState.workspaceName,
                inRegion=inRegion,
                currentBranch=getCurrentBranch(),
                needsUpgrade=_pipUpgradeInfo(api.mostRecentVersion),
                warningsList=[])


def _printLoginMessage(api: MbApi, source: Optional[str] = None, displayId: Optional[str] = None) -> None:
  if source is None:
    source = "notebook" if inNotebook() else "terminal"
  linkUrl = api.getLoginLink(source, getCurrentBranch())
  displayUrl = f'modelbit.com/t/{(api.authToken or "")[0:10]}...'
  printTemplate("login",
                displayId,
                displayUrl=displayUrl,
                linkUrl=linkUrl,
                source=source,
                needsUpgrade=_pipUpgradeInfo(api.mostRecentVersion))


def _pipUpgradeInfo(mostRecentVersion: Optional[str]) -> Optional[Dict[str, str]]:
  if inDeployment() or mostRecentVersion is None:
    return None  # runtime environments don't get upgraded
  latestVer = mostRecentVersion

  def ver2ints(ver: str) -> List[int]:
    return [int(v) for v in ver.split(".")]

  nbVer = pkgVersion
  if latestVer and ver2ints(latestVer) > ver2ints(nbVer):
    return {"installed": nbVer, "latest": latestVer}
  return None
