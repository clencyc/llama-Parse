import logging
from typing import Dict, List, Optional

from modelbit.internal.retry import retry

from .api import MbApi

logger = logging.getLogger(__name__)


class CommonFilesApi:
  api: MbApi

  def __init__(self, api: MbApi):
    self.api = api

  @retry(4, logger)
  def addFiles(self, branch: str, files: Dict[str, str]) -> None:
    self.api.getJsonOrThrow("api/cli/v1/common_files/add", {"branch": branch, "files": files})

  @retry(4, logger)
  def listFiles(self, branch: str, prefix: Optional[str]) -> List[str]:
    return self.api.getJsonOrThrow("api/cli/v1/common_files/list", {
        "branch": branch,
        "prefix": prefix
    })["names"]

  @retry(4, logger)
  def deleteFiles(self, branch: str, names: List[str]) -> None:
    self.api.getJsonOrThrow("api/cli/v1/common_files/delete", {"branch": branch, "names": names})
