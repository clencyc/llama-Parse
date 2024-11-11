import logging
from typing import Dict, Optional, List

from .api import MbApi

logger = logging.getLogger(__name__)


class PackageVersion:

  def __init__(self, name: str, version: str):
    self.name = name
    self.version = version


class SnowparkVersions:

  def __init__(self, versions: List[Dict[str, str]]):
    self.versions = [PackageVersion(v["name"], v["version"]) for v in versions]

  def has(self, name: str, version: str) -> bool:
    for v in self.versions:
      if v.name == name and v.version == version:
        return True
    return False

  def versionsForPackage(self, name: str) -> List[str]:
    return [v.version for v in self.versions if v.name == name]


class SnowparkApi:
  api: MbApi

  def __init__(self, api: MbApi):
    self.api = api

  def getSnowparkPackageVersions(self, packageNames: List[str],
                                 pythonVersion: str) -> Optional[SnowparkVersions]:
    resp = self.api.getJsonOrThrow("api/cli/v1/snowpark/get_package_versions", {
        "packageNames": packageNames,
        "pythonVersion": pythonVersion
    })
    if not resp.get("hasSnowpark"):
      return None
    return SnowparkVersions(resp['versions']) if 'versions' in resp else None
