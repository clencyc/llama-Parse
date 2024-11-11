import io
import logging
import pickle
import pprint
import sys
import os
from contextlib import redirect_stdout
from typing import Any, Dict, Optional, cast, Union
from hashlib import sha1
import pathlib
import warnings

from modelbit.environment import getPackageForModule

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_DESCRIPTION_SIZE = 7500
MAX_DESCRIBABLE_OBJECT_SIZE = 10_000_000

NULL_BYTE = b"\x00"
ALWAYS_UPLOAD_FILE_TYPES = {".pkl", ".jlib", ".joblib", ".csv", ".tsv"}
ALWAYS_UPLOAD_FILE_SIZE = 50 * 1024
# Allow datasets and notebooks to be a little larger
# 1MB isn't too bad (Set by current POST body limit of web)
# Git sites render notebooks well and we don't currently support stubs for datasets (But might have to!)
#
UPLOAD_SIZE_EXCEPTIONS = {
    '.sql': 1024 * 1000,
    '.ipynb': 1024 * 1000,
    '.py': 1024 * 1000,
}


def calcHash(content: Union[bytes, memoryview]) -> str:
  return f"sha1:{sha1(content).hexdigest()}"


def calcHashFromFile(filePath: str) -> str:
  hasher = sha1()
  chunkSize = 50_000_000
  with open(filePath, "rb") as f:
    data = f.read(chunkSize)
    while data:
      hasher.update(data)
      data = f.read(chunkSize)
  return f"sha1:{hasher.hexdigest()}"


def shouldUploadFile(filepath: str, content: bytes) -> bool:
  _, ext = os.path.splitext(filepath)
  if ext in ALWAYS_UPLOAD_FILE_TYPES or _isBinaryFile(content):
    return True
  maxSize = UPLOAD_SIZE_EXCEPTIONS.get(ext, ALWAYS_UPLOAD_FILE_SIZE)
  return len(content) >= maxSize


def describeFile(content: bytes, maxDepth: int = 1) -> Dict[str, Any]:
  f = io.StringIO()
  with redirect_stdout(f):
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      pickleDetails = _getPickleInfo(content, maxDepth)
      if pickleDetails:
        return pickleDetails
      else:
        return describeObject(content, maxDepth)


def fileSize(filePath: str) -> int:
  return pathlib.Path(filePath).stat().st_size


def describeObject(obj: Any,
                   maxDepth: int,
                   remainingCharacters: int = MAX_DESCRIPTION_SIZE) -> Dict[str, Any]:
  return {"object": _describeObject(obj, maxDepth, remainingCharacters)}


def _truncate(text: str, remainingCharacters: int) -> str:
  if len(text) <= remainingCharacters:
    return text.strip()
  return f"{text[:remainingCharacters].strip()}... and {len(text) - remainingCharacters} more characters."


def _describeObject(obj: Any,
                    maxDepth: int,
                    remainingCharacters: int = MAX_DESCRIPTION_SIZE) -> Dict[str, Any]:
  objT: Any = type(obj)
  if (sys.getsizeof(obj) > MAX_DESCRIBABLE_OBJECT_SIZE):
    return {
        "module": _descModule(obj, objT),
        "class": _descClass(obj, objT),
        "description": "",
    }

  if objT is dict and maxDepth > 0:
    ret: Dict[str, Any] = {}
    for k, v in obj.items():
      desc = _describeObject(v, maxDepth - 1, max(0, remainingCharacters))
      remainingCharacters -= len(str(desc)) + len(str(k)) + 6  # int is for chars used in k/v format
      if remainingCharacters <= 0:
        break
      ret[k] = desc
    return ret
  elif objT is bytes:
    if _isBinaryFile(obj):
      obj = "Unknown binary file"
    else:
      obj = _decodeString(obj)
      objT = type(obj)

  if type(obj) is str:
    description = _truncate(obj, remainingCharacters)
  else:
    text = pprint.pformat(obj, depth=1, width=100, compact=True)
    description = _truncate(text, remainingCharacters)
  desc = {
      "module": _descModule(obj, objT),
      "class": _descClass(obj, objT),
      "description": description,
  }
  package = _descPackage(obj)
  if package is not None:
    desc["package"] = package
  return desc


def getFileType(filePath: str) -> str:
  if "." in filePath:
    suffix = filePath.split(".")[-1]
    if len(suffix) < 20:
      return suffix
  return "bin"


def _getPickleInfo(content: bytes, maxDepth: int) -> Optional[Dict[str, Any]]:
  try:
    import joblib
    obj = joblib.load(io.BytesIO(content))  # type: ignore
    return describeObject(obj, maxDepth)
  except Exception as e:
    logger.debug("Failed to parse as joblib", exc_info=e)
    pass

  try:
    obj = pickle.loads(content)
    return describeObject(obj, maxDepth)
  except Exception as e:
    logger.debug("Failed to parse as pickle", exc_info=e)
    return {}


def _decodeString(b: bytes) -> str:
  for encoding in ('ascii', 'utf8', 'latin1'):
    try:
      return b.decode(encoding)
    except UnicodeDecodeError:
      pass
  return b.decode('ascii', 'ignore')


def _descModule(obj: Any, objT: type) -> str:
  if hasattr(obj, "mbModuleForStub"):
    return cast(str, str(obj.mbModuleForStub))  # type: ignore
  return str(objT.__module__)


def _descPackage(objT: Any) -> Optional[str]:
  if not hasattr(objT, "__module__") or type(objT.__module__) is not str:
    return None
  baseModName = objT.__module__.split(".")[0]
  return getPackageForModule(baseModName)


def _descClass(obj: Any, objT: type) -> str:
  if hasattr(obj, "mbClassForStub"):
    return cast(str, str(obj.mbClassForStub))  # type: ignore
  return str(objT.__name__)


def _isBinaryFile(content: bytes) -> bool:
  return NULL_BYTE in content
