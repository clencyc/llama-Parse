import logging
from typing import Any, Dict, List, Optional

from modelbit.internal.secure_storage import DownloadableObjectInfo
from modelbit.internal.retry import retry

from .api import MbApi
from .common import OwnerInfo

logger = logging.getLogger(__name__)


class DatasetDesc:

  def __init__(self, data: Dict[str, Any]):
    self.name: str = data["name"]
    self.sqlModifiedAtMs: Optional[int] = data.get("sqlModifiedAtMs", None)
    self.query: str = data["query"]
    self.recentResultMs: Optional[int] = data.get("recentResultMs", None)
    self.numRows: Optional[int] = data.get("numRows", None)
    self.numBytes: Optional[int] = data.get("numBytes", None)
    self.ownerInfo = OwnerInfo(data["ownerInfo"])


class ResultDownloadInfo(DownloadableObjectInfo):

  def __init__(self, data: Dict[str, Any]):
    super().__init__(data)
    self.id: str = data["id"]

  def cachekey(self) -> str:
    return self.id


class DatasetApi:
  api: MbApi

  def __init__(self, api: MbApi):
    self.api = api

  def listDatasets(self, branch: str) -> List[DatasetDesc]:
    resp = self.api.getJsonOrThrow("api/cli/v1/datasets/list", {"branch": branch})
    datasets = [DatasetDesc(ds) for ds in resp.get("datasets", [])]
    return datasets

  @retry(4, logger)
  def getDatasetPartDownloadInfo(self, path: str) -> Optional[ResultDownloadInfo]:
    resp = self.api.getJsonOrThrow("api/cli/v1/datasets/get_part", {"path": path})
    if "downloadInfo" in resp:
      return ResultDownloadInfo(resp["downloadInfo"])
    return None
