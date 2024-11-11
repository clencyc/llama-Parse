import logging
import os, re
from typing import Any, Dict, List, Optional, Tuple, cast, Set

from .environment import listMissingPackagesFromImports, listMissingPackagesFromPipList, listLocalModulesFromImports, listInstalledPackageNames, packagesToIgnoreFromImportCheck, versionProbablyWrong, listInstalledPackages, listPrivateInstallsNotInMb
from .utils import branchFromEnv, inRuntimeJob, inDeployment, dumpJson, toNormalBranch
from .ux import MismatchedPackageWarning, MissingPackageFromImportWarning, MissingExtraFileWarning, WarningErrorTip, ProbablyNotAPackageWarning, ProbablyWantDataframeModeWarning, ProbablyVersionWrong, printTemplate, ProbablyWrongRequirement, SkippedPackageWarning
from .error import UserFacingError

pkgVersion: str = ""  # set in __init__
_currentBranch = branchFromEnv() if inRuntimeJob() else "main"

logger = logging.getLogger(__name__)


def normalizeName(n: str) -> str:
  return re.sub(r"[-_.]+", "-", n).lower()


class RuntimePythonProps:
  excludeFromDict: List[str] = ['errors']

  def __init__(self) -> None:
    self.source: Optional[str] = None
    self.name: Optional[str] = None
    self.argNames: Optional[List[str]] = None
    self.argTypes: Optional[Dict[str, str]] = None
    self.namespaceVarsDesc: Optional[Dict[str, str]] = None
    self.namespaceFunctions: Optional[Dict[str, str]] = None
    self.namespaceImports: Optional[Dict[str, str]] = None
    self.namespaceFroms: Optional[Dict[str, str]] = None
    self.namespaceModules: Optional[List[str]] = None
    self.errors: Optional[List[str]] = None
    self.namespaceVars: Optional[Dict[str, Any]] = None
    self.namespaceConstants: Optional[Dict[str, str]] = None
    self.customInitCode: Optional[List[str]] = None
    self.extraDataFiles: Optional[Dict[str, Tuple[Any, bytes]]] = None
    self.extraSourceFiles: Optional[Dict[str, str]] = None
    self.job: Optional[JobProps] = None
    self.userClasses: List[str] = []
    self.isAsync: bool = False
    self.isDataFrameMode: bool = False
    self.discoveredExtraFiles: Dict[str, str] = {}
    self.discoveredExtraDirs: Dict[str, None] = {}

  def __repr__(self) -> str:
    return dumpJson(self.__dict__)


class JobProps:

  def __init__(self, name: str, rtProps: RuntimePythonProps, schedule: Optional[str],
               emailOnFailure: Optional[str], refreshDatasets: Optional[List[str]], size: Optional[str],
               timeoutMinutes: Optional[int], arguments: Optional[List[str]]):
    self.name = name
    self.rtProps = rtProps
    self.schedule = schedule
    self.emailOnFailure = emailOnFailure
    self.refreshDatasets = refreshDatasets
    self.size = size
    self.timeoutMinutes = timeoutMinutes
    self.arguments = arguments


# For instances of user defined classes. Default Pickle doesn't handle unpickling user defined
# classes because they cannot be imported, since they're defined in the notebook
class InstancePickleWrapper:

  def __init__(self, obj: Any):
    self.clsName = obj.__class__.__name__
    self.mbClassForStub = obj.__class__.__name__
    self.mbModuleForStub = obj.__class__.__module__
    if hasattr(obj, "__getstate__"):
      self.state = obj.__getstate__()
    else:
      self.state = obj.__dict__
    self.desc = str(obj)
    if self.desc.startswith("<__main"):
      self.desc = str(self.state)

  def __repr__(self) -> str:
    return self.desc

  def restore(self, restoreClass: type) -> Any:
    inst = cast(Any, restoreClass.__new__(restoreClass))  # type: ignore
    if hasattr(inst, "__setstate__"):
      inst.__setstate__(self.state)
    else:
      inst.__dict__ = self.state
    return inst


def getMissingPackageWarningsFromEnvironment(pyPackages: Optional[List[str]]) -> List[WarningErrorTip]:
  warnings: List[WarningErrorTip] = []
  missingPackages = listMissingPackagesFromPipList(pyPackages)
  if len(missingPackages) > 0:
    for mp in missingPackages:
      desiredPackage, similarPackage = mp
      if similarPackage is not None:
        warnings.append(MismatchedPackageWarning(desiredPackage, similarPackage))
  return warnings


def getMissingPackageWarningsFromImportedModules(importedModules: Optional[List[str]],
                                                 pyPackages: Optional[List[str]]) -> List[WarningErrorTip]:
  warnings: List[WarningErrorTip] = []
  missingPackages = listMissingPackagesFromImports(importedModules, pyPackages)
  for mp in missingPackages:
    importedModule, pipPackageInstalled = mp
    warnings.append(MissingPackageFromImportWarning(importedModule, pipPackageInstalled))
  return warnings


def getMissingLocalFileWarningsFromImportedModules(
    importedModules: Optional[List[str]], extraFiles: Dict[str, str]) -> List[MissingExtraFileWarning]:
  warnings: List[MissingExtraFileWarning] = []
  localModules = listLocalModulesFromImports(importedModules)
  for lm in localModules:
    missing = True
    for filePath in extraFiles.keys():
      if lm in filePath:  # using string match for simple v1
        missing = False
    if lm == "modelbit":  # modelbit isn't "installed" in dev mode, so it doesn't show in
      missing = False
    if missing:
      warnings.append(MissingExtraFileWarning(lm))
  return warnings


def getSkippedPrivatePackagesNotInMb(importedModules: Optional[List[str]],) -> List[SkippedPackageWarning]:
  warnings: List[SkippedPackageWarning] = []
  privateInstalls = listPrivateInstallsNotInMb(importedModules)
  for pm in privateInstalls:
    reason = "The package wasn't installed from PyPI and a private package with the same version was not found in Modelbit"
    warnings.append(SkippedPackageWarning(pm, reason))
  return warnings

  listPrivateInstallsNotInMb


def getProbablyNotAPackageWarnings(pyPackages: Optional[List[str]]) -> List[ProbablyNotAPackageWarning]:
  if pyPackages is None or len(pyPackages) == 0:
    return []
  ignorablePackages = packagesToIgnoreFromImportCheck(pyPackages)
  warnings: List[ProbablyNotAPackageWarning] = []
  installedPackages = set([normalizeName(p) for p in listInstalledPackageNames()])
  for packageWithVersion in pyPackages:
    if packageWithVersion.lower().startswith(("git+", "http")) or "[" in packageWithVersion:
      continue
    nameOnly = re.split("[=<>]+", packageWithVersion)[0]
    if nameOnly in ignorablePackages:
      continue
    if normalizeName(nameOnly) not in installedPackages:
      warnings.append(ProbablyNotAPackageWarning(nameOnly))
  return warnings


def warningIfShouldBeUsingDataFrameWarning(
    argNames: Optional[List[str]], argTypes: Optional[Dict[str,
                                                           str]]) -> List[ProbablyWantDataframeModeWarning]:
  if argNames is None or len(argNames) != 1 or argTypes is None:
    return []
  if argTypes.get(argNames[0], None) == "DataFrame":
    return [ProbablyWantDataframeModeWarning()]
  return []


def getVersionProbablyWrongWarnings(pyPackages: Optional[List[str]]) -> List[ProbablyVersionWrong]:
  if pyPackages is None or len(pyPackages) == 0:
    return []

  installedPackages = listInstalledPackages()

  warnings: List[ProbablyVersionWrong] = []
  for pkg in pyPackages:
    if pkg in installedPackages:
      continue
    try:
      if versionProbablyWrong(pkg):
        warnings.append(ProbablyVersionWrong(pkg))
    except Exception as err:
      logger.info("Error checking versionProbablyWrong: %s", err)
  return warnings


def setCurrentBranch(branch: str) -> None:
  global _currentBranch
  if type(branch) != str:
    raise Exception("Branch must be a string.")
  if (branch != _currentBranch):
    logger.info("Changing branch to %s", branch)
    if not inDeployment():
      printTemplate("message", None, msgText=f"Switched to '{branch}'.")
  _currentBranch = branch


def getCurrentBranch() -> str:
  global _currentBranch
  return toNormalBranch(_currentBranch)


def getDeploymentName() -> Optional[str]:
  return os.environ.get('DEPLOYMENT_NAME')


def getDeploymentVersion() -> Optional[str]:
  return os.environ.get('DEPLOYMENT_VERSION')


def mergePipPackageLists(usersList: List[str], inferredList: List[str]) -> List[str]:
  mergedList: List[str] = []
  seenPackageNames: Set[str] = set()
  for pkg in (usersList + inferredList):
    if pkg.startswith("git+https") or pkg.startswith("https"):
      mergedList.append(pkg)
    else:
      nameOnly = re.split("[=<>]+", pkg)[0]
      if nameOnly not in seenPackageNames:
        seenPackageNames.add(nameOnly)
        mergedList.append(pkg)
  return mergedList


def assertNoImpossiblePackages(namespaceModules: List[str]) -> None:
  for nm in namespaceModules:
    if "google.colab" in nm:
      raise UserFacingError(
          "The google.colab package is not installable outside of Colab. Please remove it as a dependency.")


def getProbablyWrongRequirementWarning(pyPackages: Optional[List[str]]) -> List[ProbablyWrongRequirement]:
  warnings: List[ProbablyWrongRequirement] = []
  if not pyPackages:
    return warnings

  for p in pyPackages:
    if p.startswith("ipython="):
      warnings.append(
          ProbablyWrongRequirement(
              packageName="ipython",
              reason=
              "Modelbit does not use iPython to run deployments. Including this package may be a mistake."))

  return warnings
