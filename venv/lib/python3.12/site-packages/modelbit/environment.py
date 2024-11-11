from typing import List, Tuple, Dict, Optional, Set, cast
import os, sys, json, time, re, logging
from functools import lru_cache

logger = logging.getLogger(__name__)

ALLOWED_PY_VERSIONS = ['3.6', '3.7', '3.8', '3.9', '3.10', '3.11', '3.12']

PipListCache: Tuple[float, List[Dict[str, str]]] = (0, [])
PipListCacheTimeoutSeconds = 5

ListModulesCache: Tuple[float, Dict[str, List[str]]] = (0, {})
ListModulesCacheTimeoutSeconds = 5

PrivatePackageNotInMb = "PrivatePackageNotInMb"


def clearCaches() -> None:
  global PipListCache, ListModulesCache
  PipListCache = (0, [])
  ListModulesCache = (0, {})


def listInstalledPackages() -> List[str]:
  return [f'{p["name"]}=={p["version"]}' for p in getPipList()]


def listInstalledPackageNames() -> List[str]:
  return [p["name"] for p in getPipList()]


# Returns List[(desiredPackage, installedPackage|None)]
def listMissingPackagesFromPipList(
    deploymentPythonPackages: Optional[List[str]]) -> List[Tuple[str, Optional[str]]]:
  missingPackages: List[Tuple[str, Optional[str]]] = []

  if deploymentPythonPackages is None or len(deploymentPythonPackages) == 0:
    return missingPackages

  installedPackages = listInstalledPackages()
  lowerInstalledPackages = [p.lower() for p in installedPackages]

  for dpp in deploymentPythonPackages:
    if "+" in dpp:
      continue
    if dpp.lower() not in lowerInstalledPackages:
      similarPackage: Optional[str] = None
      dppNoVersion = dpp.split("=")[0].lower()
      for ip in lowerInstalledPackages:
        if ip.split("=")[0] == dppNoVersion:
          similarPackage = ip
      missingPackages.append((dpp, similarPackage))

  return missingPackages


def getInstalledPythonVersion() -> str:
  installedVer = f"{sys.version_info.major}.{sys.version_info.minor}"
  return installedVer


def guessGitPackageName(gitUrl: str) -> str:
  return gitUrl.split("/")[-1].replace(".git", "")


def guessHttpPackageName(gitUrl: str) -> str:
  return gitUrl.split("/")[-1].split(".")[0]


def packagesToIgnoreFromImportCheck(deploymentPythonPackages: Optional[List[str]]) -> List[str]:
  ignorablePackages: List[str] = ["modelbit"]
  if deploymentPythonPackages is None:
    return ignorablePackages

  for p in deploymentPythonPackages:
    if p.endswith(".git"):
      ignorablePackages.append(guessGitPackageName(p))
    elif p.startswith("http"):
      ignorablePackages.append(guessHttpPackageName(p))
    elif "=" in p and "+" in p:
      ignorablePackages.append(p.split("=")[0])
    elif "[" in p and "]==" in p:
      ignorablePackages.append(p.split("[")[0])

  missingPackages = listMissingPackagesFromPipList(deploymentPythonPackages)
  for mp in missingPackages:
    if mp[1] is not None:
      ignorablePackages.append(mp[1].split("=")[0])

  return ignorablePackages


# Mostly to prevent adding packages that were installed with git and now have a foo==bar name
def scrubUnwantedPackages(deploymentPythonPackages: List[str]) -> List[str]:

  def normalizeName(s: str) -> str:  # meant to increase matching between guessed git names and package names
    return re.sub(r"[^a-z0-9]+", "", s.lower())

  packagesToScrub: Set[str] = set(["modelbit"])
  for p in deploymentPythonPackages:
    if p.endswith(".git"):
      packagesToScrub.add(normalizeName(guessGitPackageName(p)))

  scrubbedPackageList: List[str] = []
  for p in deploymentPythonPackages:
    if "==" in p:
      packageName = normalizeName(p.split("==")[0])
      if packageName in packagesToScrub:
        continue
    scrubbedPackageList.append(p)

  return scrubbedPackageList


def addDependentPackages(deploymentPythonPackages: List[str]) -> List[str]:
  allPackages: List[str] = []
  pkgByModule = listInstalledPackagesByModule()

  def getInstalledModule(name: str) -> Optional[str]:
    if name in pkgByModule and len(pkgByModule[name]) > 0:
      return pkgByModule[name][0]
    return None

  def hasPackage(packageName: str) -> bool:
    for d in deploymentPythonPackages:
      if d.startswith(f"{packageName}="):
        return True
    return False

  def appendIfLoaded(importName: str, packageName: Optional[str] = None) -> None:
    if hasPackage(packageName or importName):
      return  # don't add dependent package if we have it already
    pkg = pipPackageIfLoaded(importName, packageName)
    if pkg is not None:
      allPackages.append(pkg)

  for p in deploymentPythonPackages:
    allPackages.append(p)
    if p.startswith("xgboost="):
      appendIfLoaded("sklearn", "scikit-learn")
    elif p.startswith("transformers="):
      appendIfLoaded("keras")
      appendIfLoaded("tensorflow")
      appendIfLoaded("PIL", "Pillow")
      appendIfLoaded("torch")
    elif p.startswith("segment-anything=") or "segment-anything.git" in p:
      appendIfLoaded("torch")
      appendIfLoaded("torchvision")
    elif p.startswith("keras="):
      appendIfLoaded("tensorflow")
      appendIfLoaded("PIL", "Pillow")
    elif p.startswith("neptune="):
      # extras defined in https://github.com/neptune-ai/neptune-client/blob/master/pyproject.toml
      appendIfLoaded("neptune_fastai", "neptune-fastai")
      appendIfLoaded("neptune_lightgbm", "neptune-lightgbm")
      appendIfLoaded("neptune_optuna", "neptune-optuna")
      appendIfLoaded("neptune_prophet", "neptune-prophet")
      appendIfLoaded("neptune_pytorch", "neptune-pytorch")
      appendIfLoaded("neptune_sacred", "neptune-sacred")
      appendIfLoaded("neptune_sklearn", "neptune-sklearn")
      appendIfLoaded("neptune_tensorflow_keras", "neptune-tensorflow-keras")
      appendIfLoaded("neptune_tensorboard", "neptune-tensorboard")
      appendIfLoaded("neptune_xgboost", "neptune-xgboost")
    elif p.startswith("fastai="):
      appendIfLoaded("pandas")
      appendIfLoaded("torch")
    elif p.startswith("unsloth="):
      appendIfLoaded("torch")
      appendIfLoaded("xformers")
      appendIfLoaded("trl")
      appendIfLoaded("peft")
      appendIfLoaded("accelerate")
      appendIfLoaded("bitsandbytes")
    elif p.startswith("llama_cpp_python"):
      # Llama.cpp can rely on flash-attn without it being loaded in the environment
      flashInstall = getInstalledModule("flash-attn")
      torchInstall = getInstalledModule("torch")
      # we need the prebuilt wheels for flash-attn. They come from https://github.com/Dao-AILab/flash-attention/releases/
      if flashInstall and torchInstall and flashInstall.startswith("https"):
        allPackages.append(torchInstall)  # if flash-attn is installed, then torch is needed too
        allPackages.append(flashInstall)

  return allPackages


def pipPackageIfLoaded(importName: str, packageName: Optional[str] = None) -> Optional[str]:
  version = getVersionIfLoaded(importName)
  if version is not None:
    return f"{packageName or importName}=={version}"
  return None


def getVersionIfLoaded(importName: str) -> Optional[str]:
  try:
    return sys.modules[importName].__version__
  except:
    return None


def tryLoading(importName: str) -> bool:
  import importlib
  try:
    importlib.import_module(importName)
    return True
  except:
    return False


def normalizeModuleName(name: str) -> str:
  return name.replace("_", "-")


def _packageInList(packageName: str, pythonPackages: List[str]) -> bool:
  for p in pythonPackages:
    if p.startswith(packageName + "="):
      return True
  return False


# Returns List[(importedModule, pipPackageInstalled)]
def listMissingPackagesFromImports(importedModules: Optional[List[str]],
                                   deploymentPythonPackages: Optional[List[str]]) -> List[Tuple[str, str]]:
  missingPackages: List[Tuple[str, str]] = []
  ignorablePackages = packagesToIgnoreFromImportCheck(deploymentPythonPackages)
  if importedModules is None:
    return missingPackages
  if deploymentPythonPackages is None:
    deploymentPythonPackages = []

  installedModules = listInstalledPackagesByModule()
  for im in importedModules:
    baseModule = im.split(".")[0]
    baseModuleNorm = normalizeModuleName(baseModule)
    baseModuleInst = sys.modules.get(baseModule)
    if baseModuleInst is None:
      continue
    if baseModuleNorm not in installedModules:
      continue  # from stdlib or a local file, not an installed package
    pipInstalls = installedModules[baseModuleNorm]
    missingPip = True
    for pipInstall in pipInstalls:
      if pipInstall.startswith(("git+", "http")):
        if pipInstall in deploymentPythonPackages or _packageInList(baseModuleNorm, deploymentPythonPackages):
          missingPip = False
      elif "=" in pipInstall:
        pipPackage = pipInstall.split("=")[0]
        if pipInstall in deploymentPythonPackages or pipPackage in ignorablePackages:
          missingPip = False
      if pipInstall == PrivatePackageNotInMb:
        missingPip = False
    if missingPip:
      missingPackages.append((im, guessRecommendedPackage(baseModule, pipInstalls)))

  return missingPackages


def listLocalModulesFromImports(importedModules: Optional[List[str]]) -> List[str]:
  installedModules = listInstalledPackagesByModule()
  localModules: List[str] = []
  if importedModules is None:
    return []
  for im in importedModules:
    baseModule = im.split(".")[0]
    if normalizeModuleName(baseModule) not in installedModules:
      baseModuleInst = sys.modules.get(baseModule)
      if baseModuleInst is None or not hasattr(baseModuleInst, "__file__"):
        continue
      bmf = baseModuleInst.__file__
      if bmf is None or bmf.startswith((sys.base_prefix, sys.prefix)):
        continue
      localModules.append(baseModule)
  return localModules


def listPrivateInstallsNotInMb(importedModules: Optional[List[str]]) -> List[str]:
  installedModules = listInstalledPackagesByModule()
  privateModules: List[str] = []
  if importedModules is None:
    return []
  for im in importedModules:
    baseModule = im.split(".")[0]
    normBase = normalizeModuleName(baseModule)
    installedVersions = installedModules.get(normBase, [])
    if len(installedVersions) > 0 and installedVersions[0] == PrivatePackageNotInMb:
      privateModules.append(normBase)
  return privateModules


def getPackageForModule(moduleName: str) -> Optional[str]:
  packageNames = listInstalledPackagesByModule().get(normalizeModuleName(moduleName), None)
  if packageNames is not None and len(packageNames) > 0:
    return packageNames[0]
  return None


def guessRecommendedPackage(baseModule: str, pipInstalls: List[str]) -> str:
  if len(pipInstalls) == 0:
    return pipInstalls[0]

  # pandas-stubs==1.2.0.19 adds itself to the pandas module (other type packages seem to have their own base module)
  for pi in pipInstalls:
    if "types" not in pi.lower() and "stubs" not in pi.lower():
      return pi

  return pipInstalls[0]


def getModuleNames(distInfoPath: str) -> List[str]:
  try:
    topLevelPath = os.path.join(distInfoPath, "top_level.txt")
    metadataPath = os.path.join(distInfoPath, "METADATA")
    recordPath = os.path.join(distInfoPath, "RECORD")
    if os.path.exists(topLevelPath):
      with open(topLevelPath, encoding="utf-8") as f:
        recordData = f.read().strip()
        if len(recordData) > 0:
          return recordData.split("\n")

    if os.path.exists(recordPath):  # looking for their <name>/__init__.py,sha256...
      initMatcher = re.compile("^([^/]+)/__init__.py,sha")
      with open(recordPath, encoding="utf-8") as f:
        for line in f.readlines():
          match = initMatcher.search(line)
          if match:
            return [match.groups()[0]]

    if os.path.exists(metadataPath):
      with open(metadataPath, encoding="utf-8") as f:
        lines = f.read().strip().split("\n")
        for line in lines:
          if line.startswith("Name: "):
            return [line.split(":")[1].strip()]
  except:
    pass
  return []


def getPipInstallAndModuleFromDistInfo(distInfoPath: str) -> Dict[str, List[str]]:
  try:
    moduleNames = getModuleNames(distInfoPath)
    if len(moduleNames) == 0:
      return {}

    mPath = os.path.join(distInfoPath, "METADATA")
    if not os.path.exists(mPath):
      return {}

    pipName = None
    pipVersion = None
    with open(mPath, encoding="utf-8") as f:
      metadata = f.read().split("\n")
      for mLine in metadata:
        if mLine.startswith("Name: "):
          pipName = mLine.split(":")[1].strip()
        if mLine.startswith("Version: "):
          pipVersion = mLine.split(":")[1].strip()
        if pipName is not None and pipVersion is not None:
          break

    if pipName is None or pipVersion is None:
      return {}

    modulesToPipVersions: Dict[str, List[str]] = {}
    for moduleName in moduleNames:
      if moduleName not in modulesToPipVersions:
        modulesToPipVersions[moduleName] = []

    dUrl = _getGitOrHttpUrl(distInfoPath=distInfoPath, pipName=pipName, pipVersion=pipVersion)
    if dUrl is not None:
      for moduleName in moduleNames:
        modulesToPipVersions[moduleName].append(dUrl)
    else:
      for moduleName in moduleNames:
        modulesToPipVersions[moduleName].append(f"{pipName}=={pipVersion}")
    return modulesToPipVersions
  except Exception as err:
    logger.warning(f"Unable to check module '{distInfoPath}': {err}")
    return {}


def _probablyInvalidOsVersion(httpsPackagePath: str) -> bool:
  improperVersions = ["-macosx_", "-win_", "-win32_", "-musllinux_"]
  for v in improperVersions:
    if v in httpsPackagePath:
      return True
  return False


# See https://packaging.python.org/en/latest/specifications/direct-url/
def _getGitOrHttpUrl(distInfoPath: str, pipName: str, pipVersion: str) -> Optional[str]:
  directPath = os.path.join(distInfoPath, "direct_url.json")
  if not os.path.exists(directPath):
    return None
  with open(directPath, encoding="utf-8") as f:
    dJson = json.loads(f.read())
    dUrl = cast(str, dJson["url"])
    if "vcs_info" in dJson and dUrl.startswith("https"):  # can include commit if we'd like too
      if _isPublicRepo(dUrl):
        return f"git+{dUrl}"
      if not _mbHasPackage(pipName, pipVersion):
        return PrivatePackageNotInMb
      return None  # will get picked up as pipName==pipVersion
    elif dUrl.startswith("https") and not _probablyInvalidOsVersion(dUrl):
      return dUrl
    elif dUrl.startswith("file://"):
      return PrivatePackageNotInMb
  return None


def _isPublicRepo(gitUrl: str) -> bool:
  import requests
  from requests.adapters import HTTPAdapter
  from modelbit.api.api import makeRetry
  session = requests.Session()
  session.mount('http://', HTTPAdapter(max_retries=makeRetry()))
  session.mount('https://', HTTPAdapter(max_retries=makeRetry()))
  try:
    return requests.get(re.sub(r"\.git$", "", gitUrl)).status_code == 200
  except:
    return False


def _mbHasPackage(pipName: str, pipVersion: str) -> bool:
  from modelbit.internal.package import list_packages
  from modelbit.internal.auth import mbApi, isAuthenticated
  if not isAuthenticated():
    return False
  for pkg in list_packages(name=pipName, api=mbApi()):
    if pkg.version == pipVersion:
      return True
  return False


def listInstalledPackagesByModule() -> Dict[str, List[str]]:
  global ListModulesCache
  if time.time() - ListModulesCache[0] > ListModulesCacheTimeoutSeconds:
    ListModulesCache = (time.time(), _listInstalledPackagesByModule())
  return ListModulesCache[1]


def _listInstalledPackagesByModule() -> Dict[str, List[str]]:
  packages = getPipList()
  installPaths: Dict[str, int] = {}
  for package in packages:
    installPaths[package["location"]] = 1

  modulesToPipVersions: Dict[str, List[str]] = {}
  for installPath in installPaths.keys():
    try:
      for fileOrDir in os.listdir(installPath):
        if fileOrDir.endswith("dist-info"):
          dPath = os.path.join(installPath, fileOrDir)
          newModuleInfo = getPipInstallAndModuleFromDistInfo(dPath)
          for mod, pips in newModuleInfo.items():
            normMod = normalizeModuleName(mod)
            if normMod not in modulesToPipVersions:
              modulesToPipVersions[normMod] = []
            for pip in pips:
              modulesToPipVersions[normMod].append(pip)
    except Exception as err:
      # See https://gitlab.com/modelbit/modelbit/-/issues/241
      print(f"Warning, skipping module '{installPath}': {err}")
      pass

  return modulesToPipVersions


def getPipList() -> List[Dict[str, str]]:
  global PipListCache
  if time.time() - PipListCache[0] > PipListCacheTimeoutSeconds:
    PipListCache = (time.time(), _getPipList())
  return PipListCache[1]


def _getPipList() -> List[Dict[str, str]]:
  try:
    packages: List[Dict[str, str]] = []
    # need importlib_metadata imported to annotate metadata.distributions()
    import importlib_metadata  # type: ignore
    from importlib import metadata
    for i in metadata.distributions():
      iPath = str(i._path)  # type: ignore
      dirPath = os.path.dirname(iPath)
      if dirPath == "" or i.name is None:  # type: ignore
        continue
      packages.append({
          # name is added by importing importlib_metadata
          "name": i.name,  # type: ignore
          "version": i.version,
          "location": dirPath
      })
    return packages
  except Exception as err:
    print("Warning: Falling back to pip to resolve local packages.", err)
    # Some of the above isn't supported on Python 3.7, so fall back to good ol'pip
    return json.loads(os.popen("pip list -v --format json --disable-pip-version-check").read().strip())


def systemPackagesForPips(pipPackages: Optional[List[str]],
                          userSysPackages: Optional[List[str]]) -> Optional[List[str]]:
  systemPackages: Set[str] = set(userSysPackages or [])
  if pipPackages is None:
    return None
  # Add to this list as we find more dependencies that packages need
  lookups: Dict[str, List[str]] = {
      "fasttext": ["build-essential"],
      "osqp": ["cmake", "build-essential"],
      "psycopg2": ["libpq5", "libpq-dev"],
      "opencv-python": ["python3-opencv"],
      "opencv-python-headless": ["python3-opencv"],
      "opencv-contrib-python": ["python3-opencv"],
      "xgboost": ["libgomp1"],
      "lightgbm": ["libgomp1"],
      "groundingdino-py": ["build-essential"],
      "pycaret": ["libgomp1"],
      "lightfm": ["build-essential"],
      "ultralytics": ["build-essential", "libgl1", "libgl1-mesa-glx", "libglib2.0-0"],
      "openprompt": ["build-essential"],
      "sentencepiece": ["build-essential"],
      "numpy": ["build-essential"],
  }
  for pipPackage in pipPackages:
    name = pipPackage.split("=")[0].lower()
    for sysPkg in lookups.get(name, []):
      systemPackages.add(sysPkg)
    if pipPackage.startswith("git+"):
      systemPackages.add("git")

  if (len(systemPackages)) == 0:
    return None
  return sorted(list(systemPackages))


def versionProbablyWrong(pyPackage: str) -> bool:
  import urllib.request
  if pyPackage.startswith("git") or "+" in pyPackage or "==" not in pyPackage:
    return False
  name, version = pyPackage.split("==", 1)
  with urllib.request.urlopen(f"https://pypi.org/simple/{normalizeModuleName(name)}/") as uf:
    return f"-{version}" not in uf.read().decode("utf8")


def annotateSpecialPackages(deploymentPythonPackages: List[str]) -> List[str]:

  def anno(p: str) -> str:
    if p.startswith("torch==") and "+" not in p:
      if ";" in p:
        torchWithVersion, rest = p.split(";", 1)
        return f"{maybeAddCudaVersionToTorch(torchWithVersion)};{rest}"
      else:
        return maybeAddCudaVersionToTorch(p)
    elif p.startswith("jax==") and "+" not in p and "[" not in p:
      version = p.split("==")[1]
      return f"jax[cuda11_pip]=={version}"  # add GPU support to jax
    else:
      return p

  return [anno(p) for p in deploymentPythonPackages]


@lru_cache(None)  # python 3.7 doesn't have @cache
def torchPackagesRepoHtml() -> str:
  import requests
  return requests.get("https://download.pytorch.org/whl/torch/").text


def maybeAddCudaVersionToTorch(torchWithVersion: str) -> str:
  supportedCudas = ["cu121", "cu118", "cu117", "cu116"]
  repoHtml = torchPackagesRepoHtml()
  for cudaVersion in supportedCudas:
    torchRepoFormat = torchWithVersion.replace('==', '-')
    if f"{torchRepoFormat}+{cudaVersion}" in repoHtml:
      return f"{torchWithVersion}+{cudaVersion}"
  return torchWithVersion


# Some packages need to be installed after other packages
def orderPackages(packages: List[str]) -> List[str]:
  installFirst: List[str] = []
  installLast: List[str] = []
  for p in packages:
    if "flash_attn" in p:
      installLast.append(p)
    else:
      installFirst.append(p)
  return installFirst + installLast
