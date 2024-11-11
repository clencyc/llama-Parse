import logging
import os
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

from modelbit import __version__
from modelbit.error import UserFacingError
from modelbit.internal.local_config import updateWorkspaceCluster
from modelbit.utils import dumpJson, sizeOfFmt

if TYPE_CHECKING:
  from requests.adapters import Retry

logger = logging.getLogger(__name__)

ApiErrorCode_WorkspaceNotAuthedOrFound = 4
ApiErrorCode_BranchNotAuthedOrFound = 5
ApiErrorCode_BadParameter = 6
ApiErrorCode_ObjectNotFound = 7
ApiErrorCode_RateLimited = 8
ApiErrorCode_BranchProtected = 9
ApiErrorCode_PermissionDenied = 10
UserFacingErrorCodes = (ApiErrorCode_WorkspaceNotAuthedOrFound, ApiErrorCode_BranchNotAuthedOrFound,
                        ApiErrorCode_BadParameter, ApiErrorCode_ObjectNotFound, ApiErrorCode_RateLimited,
                        ApiErrorCode_BranchProtected, ApiErrorCode_PermissionDenied)
defaultRequestTimeout = 90
_MAX_DATA_LEN = 50_000_000


class NotebookEnv:

  def __init__(self, data: Dict[str, Any]):
    self.userEmail: Optional[str] = data.get("userEmail", None)
    self.signedToken: Optional[str] = data.get("signedToken")
    self.authenticated: bool = data.get("authenticated", False)
    self.workspaceName: Optional[str] = data.get("workspaceName", None)
    self.mostRecentVersion: Optional[str] = data.get("mostRecentVersion", None)
    self.cluster: Optional[str] = data.get("cluster", None)


def makeRetry(backoffFactor: float = 0.1) -> 'Retry':
  from requests.adapters import Retry
  try:
    return Retry(total=5,
                 backoff_factor=backoffFactor,
                 status_forcelist=[500, 502, 503, 504, 429],
                 allowed_methods=None)
  except:  # for versions of urllib3 < 1.26.0
    return Retry(total=5, backoff_factor=backoffFactor, status_forcelist=[500, 502, 503, 504, 429])


class MbApi:

  DEFAULT_CLUSTER = "us-east-2.aws.modelbit.com"

  _cluster = ""
  _region = ""
  _api_host = ""
  _login_host = ""

  loginState: Optional[NotebookEnv] = None
  mostRecentVersion: Optional[str] = None

  def __init__(self, authToken: Optional[str] = None, cluster: Optional[str] = None):
    if cluster is not None:
      self.setUrls(cluster)
    else:
      self.setUrls(os.getenv("MB_JUPYTER_CLUSTER", self.DEFAULT_CLUSTER))
    self.pkgVersion = __version__
    self.authToken = authToken
    self._session: Any = None

  def _initSessionOnce(self) -> None:
    if self._session is not None:
      return
    import requests
    from requests.adapters import HTTPAdapter
    self._session = requests.Session()
    self._session.mount('http://', HTTPAdapter(max_retries=makeRetry()))
    self._session.mount('https://', HTTPAdapter(max_retries=makeRetry()))

  def isAuthenticated(self) -> bool:
    if self.loginState is None:
      return False
    try:
      return self.getJson("api/cli/v1/auth/check").get("ok", False)
    except:
      return True  # during rollout, check won't work. So fall back to presence of self.loginState

  def getToken(self) -> None:
    resp = self.getJson("api/cli/v1/get_token")
    if not "signedUuid" in resp:
      raise Exception("Invalid response from server.")
    self.authToken = resp["signedUuid"]
    self.mostRecentVersion = resp.get("mostRecentVersion", None)

  def setToken(self, token: str) -> None:
    self.authToken = token
    self.loginState = None

  def getLoginLink(self, source: str, branch: str) -> str:
    if not self.authToken:
      self.getToken()
    return f'{self._login_host}t/{self.authToken}?source={source}&branch={branch}'

  def loginWithApiKey(self, apiKey: str, workspaceName: str, source: str) -> Optional[NotebookEnv]:
    for _ in range(3):  # Retry required for redirect to other clusters
      resp = self.getJson("api/cli/v1/get_token_from_api_key", {
          "apiKey": apiKey,
          "workspaceName": workspaceName,
          "source": source
      })
      if "cluster" in resp:  # Redirect to other cluster, handled by retry
        self.setUrls(resp["cluster"])
      if "notebookEnv" in resp:
        return self.setLoginState(NotebookEnv(resp["notebookEnv"]))
    return None

  def setLoginState(self, loginState: NotebookEnv) -> NotebookEnv:
    self.loginState = loginState
    self.setUrls(self.loginState.cluster)
    self.authToken = self.loginState.signedToken
    self.mostRecentVersion = self.loginState.mostRecentVersion
    return loginState

  def refreshAuthentication(self, branch: str) -> Optional[NotebookEnv]:
    resp = self.getJson("api/cli/v1/auth/refresh", {"branch": branch})
    if resp.get("errorCode", None) == ApiErrorCode_BranchNotAuthedOrFound:
      raise UserFacingError(f"Branch not found: {branch}")
    if "cluster" in resp:
      self.setUrls(resp["cluster"])
    if "notebookEnv" in resp:
      return self.setLoginState(NotebookEnv(resp["notebookEnv"]))
    return None

  def getJsonOrThrow(self, path: str, body: Dict[str, Any] = {}) -> Dict[str, Any]:
    resp = self.getJson(path, body)
    errorMsg = resp.get("errorMsg", None)
    if errorMsg:
      if resp.get("errorCode", None) in UserFacingErrorCodes:
        raise UserFacingError(errorMsg)
      raise Exception(errorMsg)
    return resp

  def getJson(self, path: str, body: Dict[str, Any] = {}) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    data.update(body)
    logger.info(f"Making request with{'out' if not self.authToken else '' } auth to {self._api_host}{path}")
    serializedData = dumpJson(data)
    dataLen = len(serializedData)
    if (dataLen > _MAX_DATA_LEN):
      raise UserFacingError(
          f'API Error: Request is too large. (Request is {sizeOfFmt(dataLen)} Limit is {sizeOfFmt(_MAX_DATA_LEN)})'
      )

    response = self._doPost(url=f'{self._api_host}{path}', headers=self._headers(), data=serializedData)
    migratedToCluster: Optional[str] = response.get('migratedToCluster')
    workspaceId: Optional[str] = response.get('workspaceId')
    if migratedToCluster is not None and workspaceId is not None:
      updateWorkspaceCluster(workspaceId, migratedToCluster)
      logger.info(f"Updating cluster to {migratedToCluster}")
      self.setUrls(migratedToCluster)
      return self._doPost(url=f'{self._api_host}{path}', headers=self._headers(), data=serializedData)

    return response

  def _doPost(self, url: str, headers: Dict[str, str], data: str) -> Dict[str, Any]:
    from requests.exceptions import ConnectionError
    self._initSessionOnce()
    try:
      with self._session.post(url, headers=headers, data=data, timeout=defaultRequestTimeout) as respObj:
        resp: Any = respObj.json()  # type: ignore
        return resp
    except ConnectionError as err:
      logger.error(str(err))
      raise UserFacingError(f"Failure when connecting to '{self._api_host}'")

  def _headers(self) -> Dict[str, str]:
    hdrs: Dict[str, str] = {"Content-Type": "application/json"}
    if self.authToken is not None:
      hdrs["Authorization"] = self.authToken
      # TODO: Send custom user-agent with pkg version + python version
      hdrs["x-mb-package-version"] = self.pkgVersion
    return hdrs

  def setUrls(self, cluster: Optional[str]) -> None:
    logger.info(f"Setting cluster to {cluster}")
    if cluster is None:
      return
    self._cluster = cluster
    self._region = self._cluster.split(".")[0]
    if cluster == "localhost":
      self._api_host = f'http://localhost:3000/'
      self._login_host = f'http://localhost:3000/'
    elif cluster == "web":
      self._api_host = f'http://web:3000/'
      self._login_host = f'http://localhost:3000/'
    else:
      self._api_host = f'https://{self._cluster}/'
      self._login_host = self._api_host

  def getApiHost(self) -> str:
    return self._api_host

  def getCluster(self) -> str:
    return self._cluster

  def getRegion(self) -> str:
    return self._region


class ApiRateLimiter:

  def __init__(self, secondsBetweenRequests: float):
    self.nextRequestAllowedAt = time.time()
    self.secondsBetweenRequests = secondsBetweenRequests

  def maybeDelay(self) -> None:
    delayTime = self.nextRequestAllowedAt - time.time()
    if delayTime > 0:
      time.sleep(delayTime)
    self.nextRequestAllowedAt = time.time() + self.secondsBetweenRequests
    return


writeLimiter = ApiRateLimiter(5)
readLimiter = ApiRateLimiter(0.1)
