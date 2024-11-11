from modelbit.api import MbApi
from modelbit.api.runtime_api import RuntimeApi
from modelbit.error import UserFacingError
from modelbit.ux import printTemplate
from typing import Union


def restartRuntime(api: MbApi, branch: str, runtimeName: str, runtimeVersion: Union[str, int]) -> None:
  if type(runtimeVersion) is str and runtimeVersion.isdigit():
    runtimeVersion = int(runtimeVersion)
  _assertRestartArgs(branch=branch, runtimeName=runtimeName, runtimeVersion=runtimeVersion)
  resp = RuntimeApi(api).restartSlots(branch=branch, runtimeName=runtimeName, runtimeVersion=runtimeVersion)
  displayName = f"{runtimeName}/{runtimeVersion}"
  if resp.alreadyInProgress():
    message = f"A restart is already in-progress for deployment '{displayName}'."
  else:  # we're treating no slots as a successful restart to keep the metaphor simple
    message = f"Success: '{displayName}' is restarting."
  printTemplate("message", None, msgText=message)


def _assertRestartArgs(branch: str, runtimeName: str, runtimeVersion: Union[str, int]) -> None:
  if type(branch) is not str or not branch:
    raise UserFacingError(f"The branch parameter must be a string.")
  if type(runtimeName) is not str or not runtimeName:
    raise UserFacingError(f"The name parameter must be a string.")
  if type(runtimeVersion) is not int and runtimeVersion != "latest":
    raise UserFacingError(f"The version parameter must be an integer or 'latest'.")
