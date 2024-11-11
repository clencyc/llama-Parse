import logging
from enum import Enum
from typing import Any, Dict, List

from .api import MbApi

logger = logging.getLogger(__name__)


class WhType(Enum):
  Snowflake = 'Snowflake'
  Redshift = 'Redshift'


class WarehouseDesc:

  def __init__(self, data: Dict[str, Any]):
    self.type: WhType = data["type"]
    self.id: str = data["id"]
    self.displayName: str = data["displayName"]
    self.deployStatusPretty: str = data["deployStatusPretty"]
    self.createdAtMs: int = data["createdAtMs"]


class WarehouseApi:
  api: MbApi

  def __init__(self, api: MbApi):
    self.api = api

  def listWarehouses(self, branch: str) -> List[WarehouseDesc]:
    resp = self.api.getJsonOrThrow("api/cli/v1/warehouses/list", {"branch": branch})
    datasets = [WarehouseDesc(ds) for ds in resp.get("warehouses", [])]
    return datasets
