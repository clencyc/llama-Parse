import gzip
import logging
import os
import pickle
import re
import sys
import time
import tempfile
import json
import random
from types import ModuleType
from typing import Any, Callable, Dict, Optional, Tuple, Union, cast, List, TextIO, Generator, Iterator, Set
from io import StringIO
from modelbit.error import UserFacingError
from contextlib import contextmanager
import inspect
from tqdm import tqdm

_deserializeCache: Dict[str, Any] = {}
logger = logging.getLogger(__name__)

showStatusThresholdBytes = 25_000_000


# From https://stackoverflow.com/questions/1094841/get-human-readable-version-of-file-size
def sizeOfFmt(num: Union[int, Any]) -> str:
  if type(num) != int:
    return ""
  numLeft: float = num
  for unit in ["Bytes", "KB", "MB", "GB", "TB", "PB", "EB", "ZB"]:
    if abs(numLeft) < 1000.0:
      return f"{numLeft:3.0f} {unit}"
    numLeft /= 1000.0
  return f"{numLeft:.1f} YB"


def unindent(source: str) -> str:
  leadingWhitespaces = len(source) - len(source.lstrip())
  if leadingWhitespaces == 0:
    return source
  newLines = [line[leadingWhitespaces:] for line in source.split("\n")]
  return "\n".join(newLines)


def timeago(pastDateMs: int) -> str:
  nowMs = time.time() * 1000
  options: List[Dict[str, Any]] = [
      {
          "name": "second",
          "divide": 1000
      },
      {
          "name": "minute",
          "divide": 60
      },
      {
          "name": "hour",
          "divide": 60
      },
      {
          "name": "day",
          "divide": 24
      },
      {
          "name": "month",
          "divide": 30.5
      },
  ]
  currentDiff = nowMs - pastDateMs
  if currentDiff < 0:
    raise Exception("The future is NYI")
  resp = "Just now"
  for opt in options:
    currentDiff = round(currentDiff / cast(Union[float, int], opt["divide"]))
    if currentDiff <= 0:
      return resp
    pluralS = ""
    if currentDiff != 1:
      pluralS = "s"
    resp = f"{currentDiff} {opt['name']}{pluralS} ago"
  return resp


def deserializeGzip(contentHash: str, reader: Callable[..., Any]) -> Any:
  if contentHash not in _deserializeCache:
    _deserializeCache[contentHash] = pickle.loads(gzip.decompress(reader()))
  return _deserializeCache[contentHash]


def timestamp() -> int:
  from datetime import datetime
  return int(datetime.timestamp(datetime.now()) * 1000)


def getEnvOrDefault(key: str, defaultVal: str) -> str:
  osVal = os.getenv(key)
  if type(osVal) == str:
    return str(osVal)
  else:
    return defaultVal


def inDeployment() -> bool:
  return 'WORKSPACE_ID' in os.environ


def deploymentWorkspaceId() -> str:
  return os.environ['WORKSPACE_ID']


def deploymentPystateBucket() -> str:
  return os.environ['PYSTATE_BUCKET']


def deploymentPystateKeys() -> List[str]:
  return os.environ['PYSTATE_KEYS'].split()


def inRuntimeJob() -> bool:
  return 'JOB_ID' in os.environ


def branchFromEnv() -> str:
  return os.getenv("BRANCH", "main")


def inNotebook() -> bool:
  # From: https://stackoverflow.com/questions/15411967/how-can-i-check-if-code-is-executed-in-the-ipython-notebook
  # Tested in Jupyter, Hex, DeepNote and Colab
  try:
    import IPython
    return hasattr(IPython.get_ipython(), "config") and len(IPython.get_ipython().config) > 0  #type:ignore
  except (NameError, ModuleNotFoundError):
    return False


def inModelbitCI() -> bool:
  return os.getenv('MODELBIT_CI') == "1"


def inIPythonTerminal() -> bool:
  try:
    import IPython
    return IPython.get_ipython().__class__.__name__ == 'TerminalInteractiveShell'  #type:ignore
  except (NameError, ModuleNotFoundError):
    return False


def getFuncName(func: Callable[..., Any], nameFallback: str) -> str:
  fName = func.__name__
  if fName == "<lambda>":
    gDict = func.__globals__
    for k, v in gDict.items():
      try:
        if v == func:
          return k
      except:
        pass  # DataFrames don't like equality
    return nameFallback
  else:
    return func.__name__


def parseLambdaSource(func: Callable[..., Any]) -> str:
  source = getSourceStr(func)
  postLambda = source.split("lambda", 1)[-1]
  seenColon = False
  parsedSource = ""
  openParenCount = 0
  i = 0
  while i < len(postLambda):
    cur = postLambda[i]
    if not seenColon:
      if cur == ":":
        seenColon = True
    else:
      if cur == "(" or cur == "[":
        openParenCount += 1
      elif cur == ")" or cur == "]":
        if openParenCount == 0:
          break
        openParenCount -= 1
      elif cur == "," and openParenCount == 0:
        break
      parsedSource += cur
    i += 1
  return parsedSource.strip()


def convertLambdaToDef(lambdaFunc: Callable[..., Any],
                       nameFallback: str = "predict") -> Tuple[Callable[..., Any], str]:
  argNames = list(lambdaFunc.__code__.co_varnames)[0:lambdaFunc.__code__.co_argcount]
  lambdaSource = parseLambdaSource(lambdaFunc)
  funcName = getFuncName(lambdaFunc, nameFallback)
  funcSource = "\n".join([f"def {funcName}({', '.join(argNames)}):", f"  return {lambdaSource}", f""])
  exec(funcSource, lambdaFunc.__globals__, locals())
  return (locals()[funcName], funcSource)


def guessNotebookType() -> Optional[str]:
  try:
    env = os.environ

    def envKeyStartsWith(prefix: str) -> bool:
      for name in env.keys():
        if name.startswith(prefix):
          return True
      return False

    if envKeyStartsWith('HEX_'):
      return 'hex'
    elif envKeyStartsWith('COLAB_'):
      return 'colab'
    elif envKeyStartsWith('DEEPNOTE_'):
      return 'deepnote'
    elif envKeyStartsWith('VSCODE_'):
      return 'vscode'
    elif envKeyStartsWith('SPY_'):
      return 'spyder'
    elif envKeyStartsWith('JPY_'):
      return 'jupyter'
  except:
    pass
  return None


def guessOs() -> Optional[str]:
  try:
    import psutil
    if psutil.MACOS:
      return "macos"
    if psutil.WINDOWS:
      return "windows"
    if psutil.LINUX:
      return "linux"
  except:
    pass
  return None


def isDsrId(dsName: str) -> bool:
  match = re.match(r'^c[a-z0-9]{24}$', dsName)
  return match is not None


def boto3Client(kind: str) -> Any:
  import boto3  # type: ignore
  args: Dict[str, Any] = dict(modelbitUser=True)
  return boto3.client(kind, **args)  # type: ignore


def repickleFromMain(obj: Any, module: ModuleType) -> Any:
  "This functions repickles objects. The module should match, but can be at a new location."
  if module.__file__ is None:
    return obj

  import importlib
  moduleName = os.path.splitext(os.path.basename(module.__file__))[0]
  loadedModule = importlib.import_module(moduleName)
  pkl = pickle.dumps(obj)
  mainModule = sys.modules['__main__']
  try:
    sys.modules['__main__'] = loadedModule
    return pickle.loads(pkl)
  except Exception as e:
    logger.info("Failed to convert main module pickle: %s", e)
  finally:
    sys.modules['__main__'] = mainModule
  return obj


def maybePlural(count: int, singularName: str) -> str:
  if count == 1:
    return singularName
  return f"{singularName}s"


def tryPickle(obj: Any, name: str, serializer: Optional[str] = None) -> bytes:
  try:
    if serializer == "cloudpickle":
      import cloudpickle
      return cloudpickle.dumps(obj)  # type: ignore
    return pickle.dumps(obj)
  except Exception as err:
    if serializer is None:
      raise UserFacingError(
          f"""Unable to store '{name}' ({type(obj)}). Consider using serializer="cloudpickle". Learn  more at https://doc.modelbit.com/api-reference/add_model/. \n\nOriginal error: {err}"""
      )
    else:
      raise UserFacingError(f"Unable to store '{name}' ({type(obj)}): {err}")


def tryUnpickle(obj: Any, name: str, serializer: Optional[str] = None) -> Any:
  try:
    if serializer == "cloudpickle":
      import cloudpickle
      return cloudpickle.loads(obj)  # type: ignore
    return pickle.loads(obj)
  except Exception as err:
    raise UserFacingError(f"Unable to load '{name}': {err}")


# We use the pip install format to show users what version of the serializer was used
def getSerializerDesc(serializer: Optional[str] = None) -> Optional[str]:
  if serializer is None:
    return None
  elif serializer == "cloudpickle":
    import cloudpickle
    return f"cloudpickle=={cloudpickle.__version__}"
  else:
    raise UserFacingError(f"Unknown serializer: {serializer}")


def tempPath(*parts: str) -> str:
  return os.path.join(tempfile.gettempdir(), *parts)


def toUrlBranch(b: str) -> str:
  return b.replace("/", "~")


def toNormalBranch(b: str) -> str:
  return b.replace("~", "/")


# Duplicated from DataScienceEncoder in deployer/environment/modelbit_deployment_runtime.py
class DataScienceEncoder(json.JSONEncoder):

  BuiltinTypes: Set[Any] = {str, int, bool, float}

  def __init__(self, *args: Any, **kwargs: Any):
    super().__init__(*args, **kwargs)
    self.mb_markers: Set[int] = set()

  def addMarker(self, obj: Any) -> int:
    objId = id(obj)
    if objId in self.mb_markers:
      raise UserFacingError(f"Cannot serialize result: circular reference detected.")
    self.mb_markers.add(objId)
    return objId

  def typeConverters(self) -> List[Tuple[Set[Any], Callable[[Any], Any]]]:
    import numpy, pandas, datetime
    return [
        ({numpy.bool_}, lambda x: bool(x)),
        ({
            numpy.byte, numpy.ubyte, numpy.short, numpy.ushort, numpy.intc, numpy.uintc, numpy.int_,
            numpy.uint, numpy.longlong, numpy.ulonglong
        }, lambda x: int(x)),
        ({
            numpy.half,
            numpy.float16,
            numpy.single,
            numpy.double,
            numpy.longdouble,
        }, lambda x: float(x)),
        ({
            pandas.Timestamp, pandas.Timedelta, datetime.datetime, datetime.time, datetime.date,
            numpy.csingle, numpy.cdouble, numpy.clongdouble
        }, lambda x: str(x)),
        ({numpy.ndarray}, lambda x: x.tolist()),  # type: ignore
        ({pandas.DataFrame}, lambda x: x.to_dict(orient="records")),
    ]

  def scrubValues(self, obj: Any) -> Any:
    import numpy
    objType: Any = type(obj)
    if objType is float and (numpy.isnan(obj) or numpy.isinf(obj)):
      return None
    elif objType in DataScienceEncoder.BuiltinTypes:
      return obj
    elif objType is dict:
      objId = self.addMarker(obj)
      for keyName in list(obj.keys()):
        obj[keyName] = self.scrubValues(obj[keyName])
      self.mb_markers.remove(objId)
      return obj
    elif objType is list:
      objId = self.addMarker(obj)
      for idx in range(len(obj)):
        obj[idx] = self.scrubValues(obj[idx])
      self.mb_markers.remove(objId)
      return obj
    else:
      return obj

  def encode(self, o: Any, *args: Any, **kwargs: Any) -> str:
    return super().encode(self.scrubValues(o), *args, **kwargs)

  def iterencode(self, o: Any, *args: Any, **kwargs: Any) -> Iterator[str]:
    return super().iterencode(self.scrubValues(o), *args, **kwargs)

  def getTypeConverter(self, o: Any) -> Union[Callable[..., Any], None]:
    if type(o) in self.BuiltinTypes:
      return None
    for [cTypes, cFunc] in self.typeConverters():
      if type(o) in cTypes:
        return cFunc
    return None

  def convertIfPossible(self, o: Any) -> Any:
    if inspect.ismodule(o):
      raise UserFacingError(f"Unable to serialize module to JSON: {o.__name__}")
    cFunc = self.getTypeConverter(o)
    if cFunc is not None:
      return self.scrubValues(cFunc(o))
    elif hasattr(o, "__dict__"):
      return {k: self.convertIfPossible(v) for k, v in o.__dict__.items()}
    return o

  def default(self, o: Any) -> Any:
    cFunc = self.getTypeConverter(o)
    if cFunc is not None:
      return self.scrubValues(cFunc(o))
    elif hasattr(o, "__dict__"):  # convert user classes to be json serializable
      return self.convertIfPossible(o)
    return json.JSONEncoder.default(self, o)  # always throws an exception


def dumpJson(data: Any) -> str:
  # First try normal json because it's fastest. If that doesn't work, then use slower DataScienceEncoder
  try:
    return json.dumps(data, allow_nan=False)
  except Exception:
    pass

  return json.dumps(data, cls=DataScienceEncoder)


# self-removing temp file path, for when using a handle isn't an option
@contextmanager
def tempFilePath(suffix: Optional[str] = None) -> Generator[str, Any, None]:

  tf = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
  tf.close()  # windows doesn't allow multiple open handles
  os.unlink(tf.name)

  try:
    yield tf.name
  finally:
    if os.path.exists(tf.name):
      os.unlink(tf.name)


# Stores encrypted content into local cache so it doesn't need to be downloaded later
@contextmanager
def storeFileOnSuccess(finalPath: str) -> Generator[str, Any, None]:
  tmpPath = finalPath + str(random.random()).replace(".", "")
  dirName = os.path.dirname(tmpPath)
  if dirName:
    os.makedirs(dirName, exist_ok=True)
  try:
    yield tmpPath

    if os.path.exists(tmpPath):
      if os.path.exists(finalPath):
        os.unlink(finalPath)
      os.rename(tmpPath, finalPath)
  except Exception as err:
    if os.path.exists(tmpPath):  # Remove partially stored file
      os.unlink(tmpPath)
    raise err


def getStatusOutputStream(fileSize: Optional[int]) -> TextIO:
  visible = fileSize is None or showStatusThresholdBytes < fileSize
  outputStream: TextIO = sys.stdout
  if not inNotebook():  # printing to stdout breaks git's add file flow
    outputStream = sys.stderr
  if os.getenv('MB_TXT_MODE') or not visible or inDeployment():
    outputStream = StringIO()
  return outputStream


def progressBar(  # type: ignore[no-untyped-def]
    inputSize: Optional[int],
    desc: str,
    forceVisible: bool = False,
    unit: str = 'B',
    unitDivisor: int = 1024,
    barFormat: Optional[str] = None):
  return tqdm(total=inputSize,
              unit=unit,
              unit_scale=True,
              unit_divisor=unitDivisor,
              miniters=1,
              desc=desc,
              bar_format=barFormat,
              file=getStatusOutputStream(None if forceVisible else inputSize))


def ensureSuffix(line: str, suffix: str = "\n") -> str:
  if line.endswith(suffix):
    return line
  return line + suffix


def unwrapFunction(func: Callable[..., Any]) -> Callable[..., Any]:
  if hasattr(func, "__wrapped__") and inspect.isfunction(func.__wrapped__):  # type: ignore
    return func.__wrapped__  # type: ignore
  if hasattr(func, "__func__") and inspect.isfunction(func.__func__):  # type: ignore
    return func.__func__  # type: ignore
  return func


def getSourceStr(obj: Union[Callable[..., Any], type]) -> str:
  # Hex broke inspect.getsource and inspect.getsourcelines. See #2263, #2464
  sourceFile: Tuple[List[str], int] = inspect.findsource(unwrapFunction(obj))
  fileLines, startingLine = sourceFile
  fileLines = [ensureSuffix(n) for n in fileLines]
  return "".join(inspect.getblock(fileLines[startingLine:]))


def inChunks(lst: List[Any], n: int) -> List[List[Any]]:
  return [lst[i:i + n] for i in range(0, len(lst), n)]
