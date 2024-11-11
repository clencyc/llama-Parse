import inspect
import re
import sys
import os
import types
import pprint
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple, cast, Union
from types import ModuleType

from modelbit.internal.describe import MAX_DESCRIBABLE_OBJECT_SIZE
from modelbit.error import UserFacingError

from .helpers import InstancePickleWrapper, JobProps, RuntimePythonProps
from .source_generation import DefaultModuleName, makeSourceFile
from .utils import convertLambdaToDef, unindent, dumpJson, getSourceStr, unwrapFunction
from .keras_wrapper import KerasWrapper

if TYPE_CHECKING:
  from modelbit.jobs import ModelbitJobWrapper
  from modelbit.setup_manager import StoredSetup

MAX_LIST_ITEMS_TO_CHECK = 50  # if users send us a giant list we assume the items are all roughly the same
MAX_CONST_STR_LEN = 250
PRIMITIVE_TYPES: List[type] = [int, float, str, bool]


class NamespaceCollection:

  def __init__(self) -> None:
    self.functions: Dict[str, str] = {}
    self.vars: Dict[str, Any] = {}
    self.constants: Dict[str, str] = {}
    self.imports: Dict[str, str] = {}
    self.froms: Dict[str, str] = {}
    self.allModules: List[str] = []
    self.customInitCode: List[str] = []
    self.extraDataFiles: Dict[str, Tuple[Any, bytes]] = {}
    self.extraSourceFiles: Dict[str, str] = {}
    self.userClasses: Dict[str, str] = {}
    self.discoveredExtraFiles: Dict[str, str] = {}
    self.discoveredExtraDirs: Dict[str, None] = {}

  def __repr__(self) -> str:
    return dumpJson(self.__dict__)


def getRuntimePythonProps(func: Optional[Callable[..., Any]],
                          sourceOverride: Optional[str] = None,
                          job: Optional['ModelbitJobWrapper'] = None,
                          extraFiles: Optional[List[str]] = None,
                          dataframeMode: bool = False,
                          setups: List['StoredSetup'] = []) -> RuntimePythonProps:
  props: RuntimePythonProps = RuntimePythonProps()
  if inspect.ismethoddescriptor(func):
    func = _stripDecoratorWrappers(func)
  if func is not None and not inspect.isfunction(func):
    raise UserFacingError('The deploy function parameter does not appear to be a function.')
  if func is None and (job is None):
    raise UserFacingError('A deploy function or job is required.')

  nsCollection = NamespaceCollection()

  if func is not None:
    collectNamespaceDeps(func, nsCollection)
    props.name = func.__name__
    props.source = sourceOverride if sourceOverride is not None else getFuncSource(func)
    props.argNames = getFuncArgNames(func)
    props.argTypes = annotationsToTypeStr(func.__annotations__)

  if job is not None:
    collectNamespaceDeps(job.func, nsCollection)
    props.job = jobWrapperToProps(job)

  if extraFiles is not None:
    for localPath in extraFiles:
      collectModulesFromExtraFile(nsCollection, localPath)
  for localPath in nsCollection.discoveredExtraFiles.keys():
    collectModulesFromExtraFile(nsCollection, localPath)

  for setup in setups:
    setup.mergeIntoToColl(nsCollection)

  props.namespaceFunctions = nsCollection.functions
  props.namespaceVars = nsCollection.vars
  props.namespaceConstants = nsCollection.constants
  props.namespaceVarsDesc = _strValues(nsCollection.vars)
  props.namespaceImports = nsCollection.imports
  props.namespaceFroms = nsCollection.froms
  props.namespaceModules = list(set(nsCollection.allModules))
  props.customInitCode = nsCollection.customInitCode
  props.extraDataFiles = nsCollection.extraDataFiles
  props.extraSourceFiles = nsCollection.extraSourceFiles
  props.userClasses = list(nsCollection.userClasses.values())
  props.isAsync = inspect.iscoroutinefunction(func)
  props.isDataFrameMode = dataframeMode
  props.discoveredExtraFiles = nsCollection.discoveredExtraFiles
  props.discoveredExtraDirs = nsCollection.discoveredExtraDirs

  # Add modules from jobs to inference code so warnings fire if job includes extra packages
  if props.job is not None:
    if props.job.rtProps.namespaceModules is not None:
      for nMod in props.job.rtProps.namespaceModules:
        if nMod not in props.namespaceModules:
          props.namespaceModules.append(nMod)

  return props


def collectModulesFromExtraFile(coll: NamespaceCollection, filepath: str) -> None:
  try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("modelbit_tmp_module", filepath)
    if spec is None or spec.loader is None:
      return
    with redirect_stdout(StringIO()):  # consume warnings/messages made by imported files
      with redirect_stderr(StringIO()):
        try:
          mod = importlib.util.module_from_spec(spec)
          spec.loader.exec_module(mod)
        except:
          return  # couldn't import the file

    newCollection = NamespaceCollection()
    for im in inspect.getmembers(mod):
      if inspect.ismodule(im[1]):  # imports
        newCollection.allModules.append(im[0])
        newCollection.imports[im[0]] = im[1].__name__
        maybeCollectLocalModule(im[0], newCollection)
      elif inspect.isfunction(im[1]):
        collectNamespaceDeps(im[1], newCollection)
      elif inspect.isclass(im[1]):
        newCollection.allModules.append(im[1].__module__)

    # copy over just the modules since we don't need the imports or vars
    coll.allModules += newCollection.allModules
    coll.discoveredExtraFiles.update(newCollection.discoveredExtraFiles)
    coll.discoveredExtraDirs.update(newCollection.discoveredExtraDirs)
  except Exception as err:
    print(f"Warning: Unable to analyze dependencies in '{filepath}'", err)


def getFuncSource(func: Optional[Callable[..., Any]]) -> Optional[str]:
  if not inspect.isfunction(func):
    return None
  try:
    return unindent(getSourceStr(func))
  except OSError:  # probably in interactive mode
    cliSrc = getFuncSourceFromCliHistory(func.__name__)
    if cliSrc:
      return cliSrc
  raise UserFacingError(f"Could not find source code of {func.__name__}")


def getFuncSourceFromCliHistory(funcName: str) -> Optional[str]:
  import readline

  inFunction: bool = False
  funcLines: List[str] = []
  for i in range(readline.get_current_history_length()):
    cliInput = readline.get_history_item(i + 1)
    if inFunction:
      if cliInput == "" or cliInput.startswith(" ") or cliInput.startswith("\t"):
        funcLines.append(cliInput)
        continue
      else:
        inFunction = False
    if cliInput.startswith(f"def {funcName}("):
      inFunction = True
      funcLines = []
      funcLines.append(cliInput)

  if len(funcLines) > 0:
    return "\n".join(funcLines)
  else:
    return None


# This'll work some of the time. See https://gitlab.com/modelbit/modelbit/-/issues/893
def getClassSourceFromJupyterCache(cls: type) -> Optional[str]:
  for cellCode in getattr(sys.modules["__main__"], "_ih"):
    if type(cellCode) is str and f"class {cls.__name__}" in cellCode:
      return cellCode
  return None


def getClassSource(cls: type) -> str:
  try:
    from IPython.core.magics.code import extract_symbols  # type: ignore
  except:
    return getSourceStr(cls)

  originalGetfile = inspect.getfile

  # from https://stackoverflow.com/questions/51566497/getting-the-source-of-an-object-defined-in-a-jupyter-notebook
  def getfile(obj: Any) -> Optional[str]:
    if not inspect.isclass(obj):
      return originalGetfile(obj)

    # Lookup by parent module (as in current inspect)
    if hasattr(obj, '__module__'):
      obj_ = sys.modules.get(obj.__module__)
      if obj_ is not None and hasattr(obj_, '__file__'):
        return obj_.__file__

    # If parent module is __main__, lookup by methods (NEW)
    for _, member in inspect.getmembers(obj):
      if inspect.isfunction(member) and obj.__qualname__ + '.' + member.__name__ == member.__qualname__:
        fileName = inspect.getfile(member)
        if fileName == "<string>" or fileName.startswith(sys.base_prefix) or fileName.startswith(sys.prefix):
          continue
        return fileName
    return None

  inspect.getfile = getfile  # type: ignore
  try:
    fileName = getfile(cls)
    cellCode: Optional[str] = None
    if fileName:
      cellCode = "".join(inspect.linecache.getlines(fileName))  # type: ignore
    else:
      cellCode = getClassSourceFromJupyterCache(cls)
    if cellCode is None:
      raise TypeError('Source for {!r} not found'.format(cls))
    classCode = cast(str, extract_symbols(cellCode, cls.__name__)[0][0])  # type: ignore
    classCode = redecorateClass(cellCode, classCode)
  except Exception:
    import traceback
    raise TypeError(f'Source for {cls.__name__} not found: {traceback.format_exc()}')
  finally:
    inspect.getfile = originalGetfile
  return classCode


def redecorateClass(fileCode: str, classCode: str) -> str:
  fileLines = fileCode.split("\n")
  firstClassLine = classCode.split("\n")[0]
  classStartLine = fileLines.index(firstClassLine)
  walkBackIdx = classStartLine - 1
  decoratorLines: List[str] = []
  while walkBackIdx >= 0:
    if fileLines[walkBackIdx].startswith("@"):
      decoratorLines.append(fileLines[walkBackIdx])
      walkBackIdx -= 1
    else:
      break
  if len(decoratorLines) > 0:
    return "\n".join(decoratorLines) + "\n" + classCode
  return classCode


def getFuncArgNames(func: Optional[Callable[..., Any]]) -> List[str]:
  noArgs: List[str] = []
  if func is None:
    return noArgs
  func = _stripDecoratorWrappers(func)
  argSpec = inspect.getfullargspec(func)
  if argSpec.args:
    return argSpec.args
  return noArgs


def annotationsToTypeStr(annotations: Dict[str, Any]) -> Dict[str, str]:
  annoStrs: Dict[str, str] = {}
  for name, tClass in annotations.items():
    try:
      tMod = tClass.__module__
      if tClass == Any:
        annoStrs[name] = "Any"
      elif (tMod == "types" and type(tClass).__name__ == 'UnionType') or (tMod == "typing" and
                                                                          tClass.__origin__ == Union):
        nonNoneTypes = [t for t in tClass.__args__ if t.__name__ != "NoneType"]
        if len(nonNoneTypes) == 1:
          annoStrs[name] = nonNoneTypes[0].__name__
      elif tMod == "typing":
        annoStrs[name] = tClass.__dict__["_name"].lower()
      else:
        annoStrs[name] = tClass.__name__
    except:
      pass
  return annoStrs


def _strValues(args: Dict[str, Any]) -> Dict[str, str]:
  newDict: Dict[str, str] = {}
  for k, v in args.items():
    if (sys.getsizeof(v) > MAX_DESCRIBABLE_OBJECT_SIZE):
      newDict[k] = ""
      continue
    strVal = re.sub(r'\s+', " ", str(v))
    if type(v) is bytes:
      strVal = "Binary data"
    elif _isMainModule(v) and hasattr(v, "__class__"):
      strVal = f"Instance of {v.__class__.__name__}"
    elif len(strVal) > 200:
      strVal = strVal[0:200] + "..."
    newDict[k] = strVal
  return newDict


def jobWrapperToProps(jr: 'ModelbitJobWrapper') -> JobProps:
  return JobProps(name=jr.desc.jobName,
                  rtProps=getRuntimePythonProps(jr.func),
                  schedule=jr.desc.schedule,
                  emailOnFailure=jr.desc.emailOnFailure,
                  refreshDatasets=jr.desc.refreshDatasets,
                  timeoutMinutes=jr.desc.timeoutMinutes,
                  size=jr.desc.size,
                  arguments=jr.desc.arguments)


def collectNamespaceDeps(func: Callable[..., Any], collection: NamespaceCollection) -> None:
  func = _stripDecoratorWrappers(func)
  if not inspect.isfunction(func):
    return
  _collectArgTypesAndImports(func, collection)
  globalsDict = func.__globals__
  allNames = [n for n in _extractAllNames(func) if globalsDict.get(n) is not func]
  return collectNamespaceDepsFromNames(allNames=allNames, globalsDict=globalsDict, collection=collection)


def collectNamespaceDepsFromNames(allNames: List[str], globalsDict: Dict[str, Any],
                                  collection: NamespaceCollection) -> None:
  for maybeFuncVarName in allNames:
    if maybeFuncVarName not in globalsDict:
      continue
    maybeFuncVar = globalsDict[maybeFuncVarName]
    if _isMainModuleWrappedFunc(maybeFuncVar):
      maybeFuncVar = _stripDecoratorWrappers(maybeFuncVar)
    if type(maybeFuncVar).__name__ == "builtin_function_or_method":
      modName = maybeFuncVar.__module__ or maybeFuncVar.__name__
      collection.froms[maybeFuncVarName] = modName
      collection.allModules.append(modName)
    elif type(maybeFuncVar).__name__ == "_lru_cache_wrapper":
      _collectFunction(maybeFuncVar.__wrapped__, collection)  # type: ignore
    elif hasattr(maybeFuncVar, "__module__"):
      if _isMainModuleFunction(maybeFuncVar):  # the user's functions
        _collectFunction(maybeFuncVar, collection)
      elif _isMainModuleClassDef(maybeFuncVar):
        maybeFuncVar = cast(type, maybeFuncVar)
        _collectClassDefDeps(maybeFuncVar, globalsDict, collection)
      else:  # functions imported by the user from elsewhere
        if collectedSpecialObj(maybeFuncVar, maybeFuncVarName, collection):
          pass
        elif maybeFuncVar.__module__ == "typing":
          pass  # we add 'from typing import *' in the generated source
        elif inspect.isclass(maybeFuncVar):
          className = maybeFuncVar.__name__ if hasattr(maybeFuncVar, "__name__") else None
          if className is not None and maybeFuncVarName != maybeFuncVar.__name__:
            collection.froms[f"{maybeFuncVar.__name__} as {maybeFuncVarName}"] = maybeFuncVar.__module__
          else:
            collection.froms[maybeFuncVarName] = maybeFuncVar.__module__
          collection.allModules.append(maybeFuncVar.__module__)
        elif _isMainModuleClassInstance(maybeFuncVar):
          _collectClassDefDeps(maybeFuncVar.__class__, globalsDict, collection)
          collection.vars[maybeFuncVarName] = InstancePickleWrapper(maybeFuncVar)
        elif isinstance(maybeFuncVar, object) and "sklearn.pipeline" in f"{type(maybeFuncVar)}":
          collectPipelineModules(maybeFuncVar, maybeFuncVarName, globalsDict, collection)
        elif isinstance(maybeFuncVar, object) and "sklearn" in f"{type(maybeFuncVar)}" and hasattr(
            maybeFuncVar, "estimator"):
          collectSklearnEstimatorContainer(maybeFuncVar, maybeFuncVarName, collection)
        elif inspect.ismodule(maybeFuncVar):
          collection.imports[maybeFuncVarName] = maybeFuncVar.__name__
          collection.allModules.append(maybeFuncVar.__name__)
        elif isinstance(maybeFuncVar, object) and not isFunctionOrMethod(maybeFuncVar):
          collectVar(maybeFuncVar, maybeFuncVarName, collection)
        elif isFunctionOrMethod(maybeFuncVar):
          maybeFuncVar = cast(Callable[..., Any], maybeFuncVar)
          if maybeFuncVarName != maybeFuncVar.__name__:
            collection.froms[f"{maybeFuncVar.__name__} as {maybeFuncVarName}"] = maybeFuncVar.__module__
          else:
            collection.froms[maybeFuncVarName] = maybeFuncVar.__module__
          maybeCollectLocalModule(maybeFuncVar.__module__, collection)
          collection.allModules.append(maybeFuncVar.__module__)
        else:
          raise Exception(f"Unknown object type: {maybeFuncVar}")
    elif inspect.ismodule(maybeFuncVar):
      collection.imports[maybeFuncVarName] = maybeFuncVar.__name__
      maybeCollectLocalModule(maybeFuncVar.__name__, collection)
      collection.allModules.append(maybeFuncVar.__name__)
    elif inspect.isclass(maybeFuncVar):
      collection.froms[maybeFuncVarName] = maybeFuncVar.__module__  #
      collection.allModules.append(maybeFuncVar.__module__)
    elif isFunctionOrMethod(maybeFuncVar):
      collection.froms[maybeFuncVarName] = maybeFuncVar.__module__
      collection.allModules.append(maybeFuncVar.__module__)
    else:
      collectVar(maybeFuncVar, maybeFuncVarName, collection)
  collection.allModules = sorted(list(set(collection.allModules)))


def isFunctionOrMethod(obj: Any) -> bool:
  return type(obj) == types.FunctionType or type(obj) == types.MethodType


def _collectArgTypesAndImports(func: Callable[..., Any], collection: NamespaceCollection) -> None:
  try:
    import ast

    def collectObj(astObject: Any) -> None:
      if astObject is None:
        return
      elif isinstance(astObject, ast.Name):
        collectModName(astObject.id)
      elif isinstance(astObject, ast.keyword):
        collectObj(astObject.value)
      elif isinstance(astObject, ast.Attribute):
        collectObj(astObject.value)
      elif isinstance(astObject, ast.arg):
        collectObj(astObject.arg)
      elif isinstance(astObject, ast.Call):
        for a in astObject.args:
          collectObj(a)
        for k in astObject.keywords:
          collectObj(k)
        collectObj(astObject.func)
      elif isinstance(astObject, ast.Import):
        for alias in astObject.names:
          collection.allModules.append(alias.name)
      elif isinstance(astObject, ast.ImportFrom) and astObject.module is not None:
        collection.allModules.append(astObject.module)
      elif isinstance(astObject, ast.Subscript):
        collectObj(astObject.value)
        collectObj(astObject.slice)
      elif isinstance(astObject, ast.Tuple):
        for a in astObject.dims:
          collectObj(a)
      elif isinstance(astObject, ast.FunctionDef):
        for a in astObject.args.args:  # type: ignore
          if hasattr(a, "annotation"):
            collectObj(a.annotation)

    def collectModName(modName: str) -> None:
      globalsDict = func.__globals__
      if modName in globalsDict:
        gMod = globalsDict[modName]
        if _isMainModuleClassDef(gMod):
          _collectClassDefDeps(gMod, globalsDict, collection)
        elif hasattr(gMod, "__module__"):
          collection.froms[modName] = gMod.__module__
          collection.allModules.append(gMod.__module__)
        else:
          collection.imports[modName] = gMod.__name__
          collection.allModules.append(gMod.__name__)

    def collectVarName(name: str) -> None:
      if name in func.__globals__:
        collectVar(func.__globals__[name], name, collection)

    sigAst = cast(ast.FunctionDef, ast.parse(getFuncSource(func)).body[0])  # type: ignore

    for a in sigAst.args.defaults:
      if isinstance(a, ast.Name):
        collectVarName(a.id)
    for a in sigAst.args.args:  # type: ignore
      if hasattr(a, "annotation"):
        collectObj(a.annotation)
    if sigAst.returns is not None:
      collectObj(sigAst.returns)
    for d in sigAst.decorator_list:
      collectObj(d)
    for b in sigAst.body:
      collectObj(b)

  except Exception as err:
    strErr = f"{err}"
    if (strErr != "could not get source code" and func.__name__ != "<lambda>"):
      print(f"Warning: failed parsing function: {func} {err}")


def _collectClassModules(clsName: str, clsSource: str, funcGlobals: Dict[str, Any],
                         collection: NamespaceCollection) -> None:
  try:
    import ast

    def collectObj(astObject: Any) -> None:
      if astObject is None:
        return
      elif isinstance(astObject, ast.Name):
        collectModName(astObject.id)
      elif isinstance(astObject, ast.AnnAssign):
        collectObj(astObject.annotation)
        collectObj(astObject.value)
        collectObj(astObject.target)
      elif isinstance(astObject, ast.Assign):
        collectObj(astObject.value)
        for t in astObject.targets:
          collectObj(t)
      elif isinstance(astObject, ast.Subscript):
        collectObj(astObject.value)
        collectObj(astObject.slice)
      elif isinstance(astObject, ast.Call):
        collectObj(astObject.func)
        for a in astObject.args:
          collectObj(a)
      elif isinstance(astObject, ast.Attribute):
        collectObj(astObject.value)
      elif isinstance(astObject, ast.List):
        for e in astObject.elts:
          collectObj(e)
      elif isinstance(astObject, ast.Tuple):
        if hasattr(astObject, "dims"):  # not supported until python 3.9
          for d in astObject.dims:
            collectObj(d)

    def collectModName(modName: str) -> None:
      if modName in funcGlobals:
        gMod = funcGlobals[modName]
        if hasattr(gMod, "__module__"):
          if gMod.__module__ != "__main__":
            collection.froms[modName] = gMod.__module__
            collection.allModules.append(gMod.__module__)
        else:
          collection.imports[modName] = gMod.__name__
          collection.allModules.append(gMod.__name__)

    clsDef = cast(Any, ast.parse((clsSource)).body[0])  # type: ignore
    for b in clsDef.bases:
      collectObj(b)
    for d in clsDef.decorator_list:  # type: ignore
      collectObj(d)
    for b in clsDef.body:
      collectObj(b)

  except Exception as err:
    print(f"Warning: failed superclasses for {clsName}: {err}")


def _extractAllNames(func: Callable[..., Any]) -> List[str]:
  code = func.__code__
  return list(code.co_names) + list(code.co_freevars) + _extractListCompNames(func)


def _extractListCompNames(func: Callable[..., Any]) -> List[str]:
  names: List[str] = []
  for const in func.__code__.co_consts:
    if hasattr(const, "co_names"):
      for name in list(const.co_names):
        names.append(name)
  return names


def _collectFunction(func: Callable[..., Any], collection: NamespaceCollection) -> None:
  argNames = list(func.__code__.co_varnames or [])[0:func.__code__.co_argcount]
  funcSig = f"{func.__name__}({', '.join(argNames)})"
  if funcSig not in collection.functions:
    src = getFuncSource(func)
    if src:
      collection.functions[funcSig] = src
    else:
      raise UserFacingError(f"Could not collect the source code of {func.__name__}")
    collectNamespaceDeps(func, collection)


def _isWrappedFunc(func: Callable[..., Any]) -> bool:
  return (hasattr(func, "__wrapped__") and inspect.isfunction(func.__wrapped__) or  # type: ignore
          hasattr(func, "__func__") and inspect.isfunction(func.__func__))  # type: ignore


def _isMainModuleWrappedFunc(func: Callable[..., Any]) -> bool:
  while _isWrappedFunc(func):
    func = unwrapFunction(func)
  return _isMainModule(func)


def _stripDecoratorWrappers(func: Callable[..., Any]) -> Callable[..., Any]:
  while _isWrappedFunc(func):
    func = unwrapFunction(func)
  if hasattr(func, "__pydantic_validator__") and hasattr(func, "raw_function") and inspect.isfunction(
      func.raw_function):  # type: ignore
    func = func.raw_function  # type: ignore
  return func


def _isMainModule(obj: Any) -> bool:
  return hasattr(obj, "__module__") and obj.__module__ == "__main__"


def _isMainModuleFunction(func: Any) -> bool:
  return inspect.isfunction(func) and _isMainModule(func)


def _isMainModuleClassDef(cls: Any) -> bool:
  return inspect.isclass(cls) and _isMainModule(cls)


def _isMainModuleClassInstance(inst: Any) -> bool:
  return isinstance(inst, object) and hasattr(inst, "__class__") and _isMainModuleClassDef(inst.__class__)


def _collectClassDefDeps(cls: type, funcGlobals: Dict[str, Any], collection: NamespaceCollection) -> None:
  firstAncestor = [c for c in cls.__mro__ if not _isMainModule(c)][0]
  for superCls in cls.__mro__:
    if _isMainModule(superCls) and superCls != cls:
      _collectClassDefDeps(superCls, funcGlobals, collection)
  classSrc = getClassSource(cls)
  collection.userClasses[cls.__name__] = classSrc
  _collectClassModules(cls.__name__, classSrc, funcGlobals, collection)
  ancestorAttrs = [a for a in dir(firstAncestor) if a != "__init__"]
  for name in dir(cls):
    if name in ancestorAttrs or (name.startswith("__") and name != "__init__"):
      continue
    obj = getattr(cls, name)
    if hasattr(obj, "__module__") and cls.__module__ != obj.__module__:
      continue  # from parent, ignore
    collectNamespaceDeps(obj, collection)


def collectedSpecialObj(obj: Any, name: str, collection: NamespaceCollection) -> bool:
  if _inTypeStr("boto3.", obj):
    return True  # skip pickling instances of boto3 objects, it's not expected/possible. See #688
  if _inTypeStr("_FastText", obj):
    _collectFastText(obj, name, collection)
    return True
  if KerasWrapper.isKerasModel(obj):
    _collectKeras(obj, name, collection)
    return True
  return False


def collectPipelineModules(obj: Any, name: str, funcGlobals: Dict[str, Any],
                           collection: NamespaceCollection) -> None:
  import copy
  pipeline = copy.deepcopy(obj)
  collection.froms[pipeline.__class__.__name__] = pipeline.__module__
  collection.allModules.append(pipeline.__module__)
  collection.vars[name] = pipeline
  for step in pipeline.steps:
    pipelineObj = step[1]
    if not hasattr(pipelineObj, "__class__") or not hasattr(pipelineObj, "__module__"):
      continue
    if not _isMainModule(pipelineObj):
      collection.froms[pipelineObj.__class__.__name__] = pipelineObj.__module__
      collection.allModules.append(pipelineObj.__module__)

    if _isMainModuleClassInstance(pipelineObj):  # custom transformers
      pipelineObj.__module__ = DefaultModuleName  # FYI: modifying pipeline
      _collectClassDefDeps(pipelineObj.__class__, funcGlobals, collection)
      collection.vars[name] = InstancePickleWrapper(pipelineObj)
    elif pipelineObj.__class__.__name__ == "FunctionTransformer" and hasattr(pipelineObj, "func"):
      libDir = "lib"
      sourceOverride = None
      if pipelineObj.func.__name__ == "<lambda>":  # FYI: modifying pipeline
        pipelineObj.func, sourceOverride = convertLambdaToDef(pipelineObj.func, "lambda_func")
      helperProps = getRuntimePythonProps(pipelineObj.func, sourceOverride=sourceOverride)
      helperName = pipelineObj.func.__name__
      helperFile = makeSourceFile(helperProps, f"{libDir}/{helperName}", isHelper=True)
      helperModule = _makeModule(f"{libDir}.{helperName}", helperFile.contents)
      _makeModule(libDir, "")
      pipelineObj.func = helperModule.__dict__[pipelineObj.func.__name__]  # FYI: modifying pipeline
      collection.extraSourceFiles[helperFile.name] = helperFile.contents


def maybeCollectLocalModule(moduleName: str, collection: NamespaceCollection) -> None:
  module = sys.modules.get(moduleName)
  if not module or not hasattr(module, "__file__") or not module.__file__ or not os.path.exists(
      module.__file__) or os.path.basename(module.__file__).startswith("test_"):
    return

  localPath = os.path.relpath(module.__file__, os.getcwd())
  if not module.__file__.startswith(os.getcwd()) or localPath in collection.discoveredExtraFiles:
    return

  # sibling modules are likely intended to travel with the deployment
  with open(module.__file__, "r") as f:
    collection.discoveredExtraFiles[localPath] = f.read()
  collectModulesFromExtraFile(collection, module.__file__)  # for siblings that import siblings

  initPath = os.path.join(os.path.dirname(module.__file__), "__init__.py")
  if os.path.exists(initPath):
    localInitDir = os.path.relpath(os.path.dirname(initPath), os.getcwd())
    collection.discoveredExtraDirs[localInitDir] = None


def _makeModule(name: str, source: str) -> ModuleType:
  mod = types.ModuleType(name)
  exec(source, mod.__dict__)
  sys.modules[mod.__name__] = mod
  return mod


def collectSklearnEstimatorContainer(obj: Any, name: str, collection: NamespaceCollection) -> None:
  if not hasattr(obj, "estimator"):
    raise UserFacingError(f"Expecing an object with an estimator but found {obj}")
  collectVar(obj, name=name, collection=collection)
  estimator: Any = obj.estimator
  if "sklearn.pipeline" in f"{type(estimator)}" and hasattr(estimator, "steps"):
    for step in estimator.steps:
      stepObj = step[1]
      collection.allModules.append(stepObj.__module__)
  else:
    collection.allModules.append(estimator.__module__)


def _inTypeStr(name: str, obj: Any) -> bool:
  return name in f"{type(obj)}"


def _collectFastText(obj: Any, name: str, collection: NamespaceCollection) -> None:
  import os
  import tempfile
  tmpFilePath = os.path.join(tempfile.gettempdir(), "tmp.pkl")
  obj.save_model(tmpFilePath)
  with open(tmpFilePath, "rb") as f:
    collection.extraDataFiles[f"data/{name}.pkl"] = (obj, f.read())
  os.unlink(tmpFilePath)
  collection.customInitCode.append(f"""
with open('data/{name}.pkl') as f:
  pass # ensure hydration
{name} = fasttext.load_model('data/{name}.pkl')
  """.strip())
  collection.imports["fasttext"] = "fasttext"
  collection.allModules.append("fasttext")


def _collectKeras(obj: Any, name: str, collection: NamespaceCollection) -> None:
  saveFormat = KerasWrapper.saveFormat
  kerasBytes = KerasWrapper.getKerasBytes(obj, saveFormat)
  collection.extraDataFiles[f"data/{name}.{saveFormat}"] = (obj, kerasBytes)
  collection.customInitCode.append(f"{name} = keras.models.load_model('data/{name}.{saveFormat}')")
  collection.froms["keras"] = "tensorflow"
  collection.allModules.append("tensorflow")
  collection.allModules.append("keras")


def recursivelyCollectModules(obj: Any, collection: NamespaceCollection) -> None:
  if type(obj) is list:
    for i in cast(List[Any], obj)[:MAX_LIST_ITEMS_TO_CHECK]:
      recursivelyCollectModules(i, collection)
  elif type(obj) is dict:
    for i in list(cast(List[Any], obj.values()))[:MAX_LIST_ITEMS_TO_CHECK]:
      recursivelyCollectModules(i, collection)
  elif inspect.isclass(obj):
    collection.imports[obj.__module__] = obj.__module__
    collection.allModules.append(obj.__module__)
  elif isinstance(obj, object) and hasattr(obj, "__class__") and hasattr(
      obj, "__module__") and type(obj) != types.FunctionType:
    collection.froms[obj.__class__.__name__] = obj.__module__
    collection.allModules.append(obj.__module__)


def collectVar(obj: Any, name: str, collection: NamespaceCollection) -> None:
  parentInfo = findParentModule(obj)
  if parentInfo is not None:
    origName, origMod = parentInfo
    if origName != name:
      collection.froms[f"{origName} as {name}"] = origMod.__name__
    else:
      collection.froms[name] = origMod.__name__
    collection.allModules.append(origMod.__name__)
  elif not collectVarAsConstant(obj, name, collection):
    recursivelyCollectModules(obj, collection)
    collection.vars[name] = obj


def findParentModule(var: Any) -> Optional[Tuple[str, Any]]:
  if var is None or type(var) in PRIMITIVE_TYPES:
    return None  # avoid overmatching on common numbers or strings
  for mMod in list(sys.modules.values()):
    if not hasattr(mMod, "__file__") or not hasattr(mMod, "__dict__") or mMod.__file__ is None:
      continue  # skip unusual modules
    if ".test_" in mMod.__name__:
      continue  # avoid importing from test modules
    for k, v in mMod.__dict__.items():
      if k.startswith("_") or type(v) in PRIMITIVE_TYPES:
        continue
      if v is var:
        return (k, mMod)
  return None


def isBasicEnoughForConst(obj: Any) -> bool:
  if type(obj) is dict:
    obj = cast(Dict[Any, Any], obj)
    if len(obj) > MAX_LIST_ITEMS_TO_CHECK:
      return False
    for k, v in obj.items():
      if not isBasicEnoughForConst(k) or not isBasicEnoughForConst(v):
        return False
    return True
  elif type(obj) is list:
    obj = cast(List[Any], obj)
    if len(obj) > MAX_LIST_ITEMS_TO_CHECK:
      return False
    for v in obj:
      if not isBasicEnoughForConst(v):
        return False
    return True
  elif type(obj) in PRIMITIVE_TYPES or inspect.isclass(obj) or obj is None:
    return True
  return False


# Looking for <class 'module_name.ClassName'> -> module_name.ClassName
def replaceCapturedClassesWithReferences(formattedCode: str) -> str:
  return re.sub(r"<class '(.+?)'>", "\\1", formattedCode)


def collectVarAsConstant(obj: Any, name: str, collection: NamespaceCollection) -> bool:
  if not isBasicEnoughForConst(obj):
    return False
  recursivelyCollectModules(obj, collection)
  formattedCode = replaceCapturedClassesWithReferences(pprint.pformat(obj))
  if len(formattedCode) > MAX_CONST_STR_LEN:
    return False
  collection.constants[name] = formattedCode
  return True
