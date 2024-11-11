import logging
import logging.handlers
import os
import sys
import traceback
from functools import wraps
from typing import Callable, List, Optional, TypeVar, Union, cast, Dict, Any
import getpass
import socket

from modelbit.api.api import MbApi  # For perf, skip __init__
from modelbit.error import ModelbitError, UserFacingError
from modelbit.utils import inDeployment
from modelbit.ux import printTemplate
from modelbit.helpers import getCurrentBranch

logger = logging.getLogger(__name__)

_sessionId = os.urandom(8).hex()

TelemetryData = Dict[str, Union[str, int, float, bool, None]]


def sessionInfo() -> TelemetryData:
  global _sessionId

  ver = sys.version_info

  return {
      "session.id": _sessionId,
      "session.loginName": getpass.getuser(),
      "session.hostName": socket.gethostname(),
      "session.pythonVersion": f"{ver.major}.{ver.minor}.{ver.micro}",
      "session.platform": sys.platform,
      "session.cwd": os.getcwd(),
      "session.branch": getCurrentBranch(),
  }


def enableFileLogging() -> bool:
  return os.environ.get("MB_LOG", None) is not None


def initLogging() -> None:
  LOGLEVEL = os.environ.get('LOGLEVEL', 'WARNING').upper()
  streamHandler = logging.StreamHandler()
  handlers: List[logging.Handler] = [streamHandler]
  streamHandler.setLevel(LOGLEVEL)
  if enableFileLogging():
    try:
      import appdirs
      logDir = cast(str, appdirs.user_log_dir("modelbit"))  # type: ignore
      if not os.path.exists(logDir):
        os.makedirs(logDir, exist_ok=True)
      fileHandler = logging.handlers.RotatingFileHandler(os.path.join(logDir, "log.txt"),
                                                         maxBytes=10485760,
                                                         backupCount=5)
      fileHandler.setLevel(level="INFO")
      handlers.append(fileHandler)
    except Exception as e:
      print(e)
      logging.info(e)

  logging.basicConfig(level="INFO", handlers=handlers)


def logEventToWeb(api: Optional[MbApi] = None,
                  userErrorMsg: Optional[str] = None,
                  name: Optional[str] = None,
                  details: TelemetryData = {}) -> None:
  logInfo = sessionInfo()
  for k, v in details.items():
    logInfo[f"details.{k}"] = v
  if name is not None:
    logInfo["eventName"] = name

  if userErrorMsg is not None:
    errStackList = traceback.format_exception(*sys.exc_info())[1:]
    errStackList.reverse()
    errStack = "\n".join(errStackList)
    logInfo["errStack"] = errStack
    errorMsg = userErrorMsg + "\n" + "".join(errStack)
    logInfo["errorMsg"] = errorMsg

    if inDeployment():
      print(errorMsg, file=sys.stderr)  # TODO: Maybe report these to web?
      return

  from modelbit.api import MbApi
  api = api or MbApi()
  try:
    api.getJson("api/cli/v1/event", logInfo)
  except Exception as e:
    logger.info(e)


T = TypeVar("T", bound=Callable[..., Any])


def eatErrorAndLog(mbApi: Optional[MbApi], genericMsg: str) -> Callable[[T], T]:

  def decorator(func: T) -> T:

    @wraps(func)
    def innerFn(*args: object, **kwargs: object) -> T:
      error: Optional[Union[Exception,
                            str]] = None  # Store and raise outside the handler so the trace is more compact.
      try:
        return func(*args, **kwargs)
      except (KeyError, TypeError) as e:
        logEventToWeb(api=mbApi, userErrorMsg=f"{genericMsg}, {type(e)}, {str(e)}")
        error = e
      except UserFacingError as e:
        if e.logToModelbit:
          logEventToWeb(api=mbApi, userErrorMsg=e.userFacingErrorMessage)
        printTemplate("error", None, errorText=genericMsg + " " + e.userFacingErrorMessage)
        error = e.userFacingErrorMessage
      except Exception as e:
        specificError = cast(Optional[str], getattr(e, "userFacingErrorMessage", None))
        error = genericMsg + (" " + specificError if specificError is not None else "")
        logEventToWeb(api=mbApi, userErrorMsg=error)
        printTemplate("error_details", None, errorText=error, errorDetails=traceback.format_exc())
      # Convert to generic ModelbitError.
      if type(error) == str:
        raise ModelbitError(error)
      else:
        raise cast(Exception, error)

    return cast(T, innerFn)

  return decorator
