from typing import List, Union, Tuple, Optional
from .ux import WarningErrorTip, SnowparkBadPackageWarning, SnowparkBadPackageVersionWarning
from .api.snowpark_api import SnowparkApi
from .api import MbApi


def getPackageNameVersion(packageWithVersion: str) -> Union[Tuple[str, str], Tuple[None, None]]:
  try:
    if "==" in packageWithVersion and " " not in packageWithVersion:
      name, version = packageWithVersion.split("==")
      return (name, version)
  except:
    pass
  return (None, None)


def packageNames(packagesWithVersions: List[str]) -> List[str]:
  names: List[str] = []
  for p in packagesWithVersions:
    name, _ = getPackageNameVersion(p)
    if name is not None:
      names.append(name)
  return names


def getSnowparkWarnings(mbApi: MbApi, pythonVersion: str,
                        packagesWithVersions: Optional[List[str]]) -> List[WarningErrorTip]:
  warnings: List[WarningErrorTip] = []
  if packagesWithVersions is None or len(packagesWithVersions) == 0 or pythonVersion not in [
      "3.8", "3.9", "3.10", "3.11"
  ]:
    return warnings

  allVersions = SnowparkApi(mbApi).getSnowparkPackageVersions(packageNames=packageNames(packagesWithVersions),
                                                              pythonVersion=pythonVersion)
  if allVersions is None or len(allVersions.versions) == 0:
    return []
  for p in packagesWithVersions:
    name, version = getPackageNameVersion(p)
    if name and version and not allVersions.has(name, version):
      otherVersions = allVersions.versionsForPackage(name)
      if len(otherVersions) == 0:
        warnings.append(SnowparkBadPackageWarning(name))
      else:
        warnings.append(SnowparkBadPackageVersionWarning(name, version, otherVersions))

  return warnings
