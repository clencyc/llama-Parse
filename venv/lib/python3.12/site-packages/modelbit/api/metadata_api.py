import logging
from typing import Any, Dict, Optional

from .api import MbApi, writeLimiter, readLimiter

logger = logging.getLogger(__name__)


class DeployedRuntimeResponse:

  def __init__(self, data: Dict[str, Any]):
    self.runtimeOverviewUrl: Optional[str] = data.get("runtimeOverviewUrl")


class MetadataValidationResponse:

  def __init__(self, data: Dict[str, Any]):
    self.validationErrors: Optional[Dict[str, Optional[str]]] = data.get("validationErrors")

  def getError(self, fileName: str) -> Optional[str]:
    if self.validationErrors is None:
      return None
    return self.validationErrors.get(fileName)


class MetadataApi:
  api: MbApi

  def __init__(self, api: MbApi):
    self.api = api

  def _getMetadata(self, branch: str, runtimeName: str,
                   runtimeVersion: Optional[int]) -> Optional[Dict[str, Any]]:
    readLimiter.maybeDelay()
    resp = self.api.getJsonOrThrow(
        "api/cli/v1/runtimes/metadata/get_metadata",
        dict(branch=branch, runtimeName=runtimeName, runtimeVersion=runtimeVersion))
    return resp.get("metadata")

  def getSnowflakeMockReturnValue(self, branch: str, runtimeName: str,
                                  runtimeVersion: Optional[int]) -> Optional[Any]:
    metadata = self._getMetadata(branch=branch, runtimeName=runtimeName, runtimeVersion=runtimeVersion)
    if metadata is None:
      return None
    return metadata.get("runtimeInfo", {}).get("snowflakeMockReturnValue", None)

  def setSnowflakeMockReturnValue(self, branch: str, runtimeName: str,
                                  mockReturnValue: Any) -> DeployedRuntimeResponse:
    writeLimiter.maybeDelay()
    resp = self.api.getJsonOrThrow(
        "api/cli/v1/runtimes/metadata/set_snowflake_mock_return_value",
        dict(branch=branch, runtimeName=runtimeName, mockReturnValue=mockReturnValue))
    return DeployedRuntimeResponse(resp)

  def validateMetadataFiles(self, files: Dict[str, str]) -> MetadataValidationResponse:
    readLimiter.maybeDelay()
    try:
      resp = self.api.getJsonOrThrow("api/cli/v1/runtimes/metadata/validate", {"files": files})
      return MetadataValidationResponse(resp)
    except:
      return MetadataValidationResponse({})  # ignore errors if web is having trouble

  def validateJobMetadataFiles(self, files: Dict[str, str]) -> MetadataValidationResponse:
    readLimiter.maybeDelay()
    try:
      resp = self.api.getJsonOrThrow("api/cli/v1/training_jobs/metadata/validate", {"files": files})
      return MetadataValidationResponse(resp)
    except:
      return MetadataValidationResponse({})  # ignore errors if web is having trouble
