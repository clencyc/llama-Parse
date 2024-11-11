from typing import Optional, List, Dict, Union

from modelbit.api import CommonFilesApi, BranchApi, MbApi
from modelbit.internal import runtime_objects
from modelbit.helpers import getCurrentBranch
from modelbit.ux import printTemplate
from modelbit.error import UserFacingError
from modelbit.utils import maybePlural, dumpJson


def addFiles(api: MbApi,
             files: Union[str, List[str], Dict[str, str], None],
             modelbit_file_prefix: Optional[str] = None,
             strip_input_path: Optional[bool] = False) -> None:

  BranchApi(api).raiseIfProtected()

  if files is None:
    raise UserFacingError(f"The files parameter cannot be None.")

  if type(files) is not list and type(files) is not dict and type(files) is not str:
    raise UserFacingError(f"The files parameter must be a list or dict. It is a {type(files)}.")

  dataFiles = runtime_objects.prepareFileList(api,
                                              files,
                                              modelbit_file_prefix=modelbit_file_prefix,
                                              strip_input_path=strip_input_path)

  if len(dataFiles) == 0:
    raise UserFacingError("No files to add.")
  if len(dumpJson(dataFiles)) > 5_000_000:
    raise UserFacingError("Total file size exceeds maximum allowed (5MB). Use git or add fewer files.")

  CommonFilesApi(api).addFiles(getCurrentBranch(), dataFiles)
  printTemplate(
      "message",
      None,
      msgText=f"Success: {len(dataFiles)} {maybePlural(len(dataFiles), 'file')} uploaded.",
  )


def deleteFiles(api: MbApi, names: Union[List[str], str]) -> None:
  BranchApi(api).raiseIfProtected()

  if type(names) is str:
    names = [names]

  if type(names) is not list:
    raise UserFacingError(f"The file names parameter must be a list. It is a {type(names)}.")

  if len(names) == 0:
    raise UserFacingError("No files to delete.")

  CommonFilesApi(api).deleteFiles(getCurrentBranch(), names)
  printTemplate(
      "message",
      None,
      msgText=f"Success: {len(names)} {maybePlural(len(names), 'file')} deleted.",
  )


def listFiles(api: MbApi, prefix: Optional[str]) -> List[str]:
  BranchApi(api).raiseIfProtected()

  if prefix is not None and type(prefix) is not str:
    raise UserFacingError(f"The prefix parameter must be a string. It is a {type(prefix)}.")

  return CommonFilesApi(api).listFiles(getCurrentBranch(), prefix)
