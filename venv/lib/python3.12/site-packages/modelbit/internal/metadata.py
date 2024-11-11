from typing import Any, Optional, Union, List
from modelbit.api import MbApi, MetadataApi, BranchApi
from modelbit.error import UserFacingError
from modelbit.ux import printTemplate, WarningErrorTip, SnowflakeRemovingMockFunctionValue
import json


def getSnowflakeMockReturnValue(api: MbApi, branch: str, deploymentName: str,
                                deploymentVersion: Optional[Union[int, str]]) -> Optional[Any]:
  if type(branch) is not str:
    raise UserFacingError(f"branch parameter must be a string")
  if type(deploymentName) is not str:
    raise UserFacingError(f"deployment_name must be a string")
  if deploymentVersion == "latest":
    deploymentVersion = None
  if deploymentVersion is not None and type(deploymentVersion) is not int:
    raise UserFacingError(f"version must be an integer")

  return MetadataApi(api).getSnowflakeMockReturnValue(branch=branch,
                                                      runtimeName=deploymentName,
                                                      runtimeVersion=deploymentVersion)


def setSnowflakeMockReturnValue(api: MbApi, branch: str, deploymentName: str, mockReturnValue: Any) -> None:
  if type(branch) is not str:
    raise UserFacingError(f"branch parameter must be a string")
  if type(deploymentName) is not str:
    raise UserFacingError(f"deployment_name must be a string")
  checkMockReturnValue(mockReturnValue)

  BranchApi(api).raiseIfProtected()
  setResp = MetadataApi(api).setSnowflakeMockReturnValue(branch=branch,
                                                         runtimeName=deploymentName,
                                                         mockReturnValue=mockReturnValue)
  printTemplate("runtime-deployed",
                None,
                deploymentName=deploymentName,
                deployTimeWords="shortly.",
                runtimeOverviewUrl=setResp.runtimeOverviewUrl)


def checkMockReturnValue(mockReturnValue: Any) -> None:
  try:
    valueStr = json.dumps(mockReturnValue)
    if len(valueStr) > 10_000:
      raise UserFacingError("The mock return value is too large. It must be less than 10,000 characters.")
  except TypeError as err:
    raise UserFacingError(f"Unable to serialize the mock return value to JSON: {err}")


def getMetadataWarnings(api: MbApi, branch: str, deploymentName: str,
                        snowflakeMockReturnValue: Any) -> List[WarningErrorTip]:
  if snowflakeMockReturnValue is not None:
    return []
  try:
    existingMockReturnValue = getSnowflakeMockReturnValue(api,
                                                          branch=branch,
                                                          deploymentName=deploymentName,
                                                          deploymentVersion=None)
  except:
    return []  # new deployments won't have mock return values
  if existingMockReturnValue is None:
    return []

  warnings: List[WarningErrorTip] = []
  warnings.append(SnowflakeRemovingMockFunctionValue(existingMockReturnValue=existingMockReturnValue))
  return warnings
