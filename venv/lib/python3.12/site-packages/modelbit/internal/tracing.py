from modelbit.utils import inDeployment
from modelbit.error import UserFacingError
from contextlib import contextmanager
import time
from typing import Optional, Generator, Any

_canUseTracer: Optional[bool] = None

MaxTraceNameLength = 200


def nowMs() -> int:
  return int(time.time() * 1000)


def canUseTracer() -> bool:
  global _canUseTracer
  if _canUseTracer is not None:
    return _canUseTracer

  if not inDeployment():
    _canUseTracer = False
  else:
    try:
      import modelbit_tracer  # type: ignore
      _canUseTracer = True
    except:
      _canUseTracer = False
  return _canUseTracer


@contextmanager
def trace(name: str, printInNotebook: bool) -> Generator[None, Any, None]:
  if type(name) is not str:
    raise UserFacingError("Trace name must be a string.")
  if len(name) > MaxTraceNameLength:
    raise UserFacingError("Trace name must be shorter than 200 characters.")
  if canUseTracer():
    import modelbit_tracer  # type: ignore
    with modelbit_tracer.trace(name):  # type: ignore
      yield
  else:
    start = nowMs()
    try:
      yield
    finally:
      duration = nowMs() - start
      if printInNotebook:
        print(f"> '{name}' took {duration}ms")
