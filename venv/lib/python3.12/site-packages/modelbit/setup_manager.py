from typing import List, Any, Dict, cast, Generator, NoReturn
from .helpers import UserFacingError
from .utils import inDeployment
from .collect_dependencies import NamespaceCollection, collectNamespaceDepsFromNames
from .internal.tracing import trace, MaxTraceNameLength
import types, inspect, linecache
from contextlib import contextmanager
import sys
import re


# Keeps code with original indentation
def collectCode(frame: types.FrameType) -> str:
  filename = frame.f_code.co_filename
  lines: List[str] = []
  indent = 0
  i = 1

  # f_lineno is the last line of the frame in Py3.9 but the first line in py3.10
  forwardCrawl = sys.version_info.minor >= 10

  while abs(i) < 200:  # just in case we hit an infinite loop
    nextLine = linecache.getline(filename, frame.f_lineno + i)
    nextIndent = len(nextLine) - len(nextLine.lstrip())
    if indent == 0:
      indent = nextIndent
    elif (nextIndent < indent and len(nextLine.strip()) > 0) or nextLine == "":
      break

    if forwardCrawl:
      lines.append(nextLine)
      i += 1
    else:
      lines.insert(0, nextLine)
      i -= 1

  return "".join(lines).rstrip() + "\n"


def collectDeps(frame: types.FrameType) -> NamespaceCollection:
  coll = NamespaceCollection()
  collectNamespaceDepsFromNames(list(frame.f_code.co_names), frame.f_globals, coll)
  return coll


def raiseNotFound(setupName: str) -> NoReturn:
  raise UserFacingError(f"Unable to find modelbit.setup named '{setupName}'")


class StoredSetup():

  def __init__(self, name: str, cf: types.FrameType):
    self.name = name
    self.code = collectCode(cf)
    self.deps = collectDeps(cf)

  def definedVars(self) -> Dict[str, Any]:
    vars: Dict[str, Any] = {}
    for k, v in self.deps.vars.items():
      if self.varIsDefinedWithin(k):
        vars[k] = v
    for k, v in self.deps.constants.items():
      if self.varIsDefinedWithin(k):
        vars[k] = v
    return vars

  def toInitCode(self) -> str:
    return f'with modelbit.setup("{self.name}"):\n' + self.code

  def mergeIntoToColl(self, coll: NamespaceCollection) -> None:

    # we assume variables discovered within the setup are owned by the setup and shouldn't be pickled
    for varName in self.definedVars().keys():
      if varName in coll.constants:
        del coll.constants[varName]
      if varName in coll.vars:
        del coll.vars[varName]

    coll.allModules += self.deps.allModules
    coll.imports.update(self.deps.imports)
    coll.froms.update(self.deps.froms)
    coll.functions.update(self.deps.functions)
    coll.userClasses.update(self.deps.userClasses)
    coll.discoveredExtraDirs.update(self.deps.discoveredExtraDirs)
    coll.discoveredExtraFiles.update(self.deps.discoveredExtraFiles)
    coll.userClasses.update(self.deps.userClasses)
    coll.customInitCode += self.deps.customInitCode

    coll.customInitCode.append(self.toInitCode())

  # See #2121. We're trying to tell the difference between variables assigned in the block and those only referenced in the block
  def varIsDefinedWithin(self, varName: str) -> bool:
    rv = re.escape(varName)
    for line in self.code.split("\n"):
      if re.search(rf"{rv}\s*=|{rv},.*=", line):
        return True
    return False


def selectAndValidateSetups(setup: Any) -> List[StoredSetup]:
  if setup is None:
    return []

  setupNames: List[Any] = setup if type(setup) is list else [setup]
  selectedSetups: List[StoredSetup] = []

  for name in setupNames:
    if type(name) is not str:
      raise UserFacingError(
          f"The setup= parameter expects string values. Found: '{name}' of type {type(name)}")
    elif name not in storedSetups:
      raiseNotFound(name)
    else:
      selectedSetups.append(storedSetups[name])
  return selectedSetups


storedSetups: Dict[str, StoredSetup] = {}


@contextmanager
def setupManager(name: str) -> Generator[None, Any, None]:
  if type(name) is not str or name == "":
    raise UserFacingError("The name= parameter must be a string.")
  if '"' in name:
    raise UserFacingError("The name= parameter cannot include quotes.")
  if len(name) > MaxTraceNameLength:
    raise UserFacingError("The name= must be shorter than 200 characters.")

  with trace(f"Setup: {name}", False):
    yield

  if not inDeployment():
    # Jump back some frames: setupManager() > contextmanager.__exit__() > user's code
    cf = cast(types.FrameType, inspect.currentframe().f_back.f_back)  # type: ignore
    storedSetups[name] = StoredSetup(name, cf)
