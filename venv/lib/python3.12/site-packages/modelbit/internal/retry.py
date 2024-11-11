import logging
import os
import time
from functools import wraps
from typing import Any, Callable, Optional, TypeVar, cast

from modelbit.error import NonRetryableError, UserFacingError
from modelbit.telemetry import logEventToWeb
from modelbit.api import MbApi

T = TypeVar("T", bound=Callable[..., Any])


def retry(retries: int, logger: Optional[logging.Logger]) -> Callable[[T], T]:

  def decorator(func: T) -> T:
    if os.getenv("NORETRY", None):
      return func

    @wraps(func)
    def innerFn(*args: Any, **kwargs: Any) -> Any:
      lastError: Optional[Exception] = None
      for attempt in range(retries):
        try:
          return func(*args, **kwargs)
        except NonRetryableError:
          raise
        except UserFacingError:
          raise
        except Exception as e:
          if logger:
            logger.info("Retrying:  got %s", e)
          if len(args) > 0 and isinstance(args[0], MbApi):
            logEventToWeb(api=args[0], userErrorMsg=str(e))
          elif 'api' in kwargs and isinstance(kwargs['api'], MbApi):
            logEventToWeb(api=kwargs['api'], userErrorMsg=str(e))
          else:
            logEventToWeb(userErrorMsg=str(e))
          lastError = e
          retryTime = 2**attempt
          if logger and attempt > 2:
            logger.warning("Retrying in %ds: %s", retryTime, str(e))
          time.sleep(retryTime)
      if lastError is None:
        raise Exception(f"Failed after {retries} retries. Please contact support.")
      raise lastError

    return cast(T, innerFn)

  return decorator
