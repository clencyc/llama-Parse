import os
from typing import Any, Dict, Optional, cast

import appdirs
from yaml import safe_dump, safe_load

AppDirs = appdirs.AppDirs("modelbit")


class ModelbitWorkspace:

  def __init__(self, data: Dict[str, Any]):
    self.gitUserAuthToken: str = data["gitUserAuthToken"]
    self.cluster: str = data["cluster"]

  def to_dict(self) -> Dict[str, str]:
    return {"cluster": self.cluster, "gitUserAuthToken": self.gitUserAuthToken}


class ModelbitConfig:

  def __init__(self, data: Dict[str, Any]):
    self.workspaces: Dict[str, ModelbitWorkspace] = dict()
    if data and "workspaces" in data and type(data["workspaces"]) == dict:
      self.workspaces = {k: ModelbitWorkspace(v) for k, v in cast(Dict[str, Any], data["workspaces"]).items()}

  def get(self, workspaceId: str) -> Optional[ModelbitWorkspace]:
    return self.workspaces.get(workspaceId, None)

  def to_dict(self) -> Dict[str, Any]:
    return {"workspaces": {k: v.to_dict() for k, v in self.workspaces.items()}}


def configPath() -> str:
  return os.path.join(AppDirs.user_config_dir, "credentials.yaml")  # type: ignore


def saveConfig(config: ModelbitConfig) -> None:
  filepath = configPath()
  os.makedirs(os.path.dirname(filepath), exist_ok=True)
  with open(filepath, "w") as f:
    safe_dump(config.to_dict(), f)


def loadConfig() -> ModelbitConfig:
  try:
    with open(configPath(), "r") as f:
      configDict: Dict[str, Any] = safe_load(f)
  except FileNotFoundError:
    configDict = {}
  return ModelbitConfig(configDict)


def getCacheDir(workspaceId: str, kind: str) -> str:
  try:
    cacheDir = os.path.join(AppDirs.user_cache_dir, workspaceId, kind)  # type: ignore
    os.makedirs(cacheDir, exist_ok=True)
    return cacheDir
  except:  # maybe a read-only file system
    import tempfile
    cacheDir = os.path.join(tempfile.gettempdir(), workspaceId, kind)
    os.makedirs(cacheDir, exist_ok=True)
    return cacheDir


def getWorkspaceConfig(workspaceId: str) -> Optional[ModelbitWorkspace]:
  allConfig = loadConfig()
  return allConfig.get(workspaceId)


def saveWorkspaceConfig(workspaceId: str, cluster: str, gitUserAuthToken: str) -> None:
  allConfig = loadConfig()
  allConfig.workspaces[workspaceId] = ModelbitWorkspace({
      "cluster": cluster,
      "gitUserAuthToken": gitUserAuthToken
  })
  saveConfig(allConfig)


def updateWorkspaceCluster(workspaceId: str, cluster: str) -> bool:
  allConfig = loadConfig()
  if workspaceId not in allConfig.workspaces:
    return False
  allConfig.workspaces[workspaceId].cluster = cluster
  saveConfig(allConfig)
  return True


def deleteWorkspaceConfig(workspaceId: str) -> None:
  allConfig = loadConfig()
  if workspaceId not in allConfig.workspaces:
    return
  del (allConfig.workspaces[workspaceId])
  saveConfig(allConfig)
