from typing import List

from modelbit.utils import timeago
from modelbit.ux import TableHeader, renderTemplate
from modelbit.api import WarehouseApi, WarehouseDesc, MbApi
from modelbit.helpers import getCurrentBranch


class WarehousesList:

  def __init__(self, mbApi: MbApi):
    self._warehouses: List[WarehouseDesc] = []
    self._warehouses = WarehouseApi(mbApi).listWarehouses(getCurrentBranch())
    self._isAuthenticated = mbApi.isAuthenticated()

  def _repr_html_(self) -> str:
    if not self._isAuthenticated:
      return ""
    return self._makeWarehousesHtmlTable()

  def _makeWarehousesHtmlTable(self) -> str:
    if len(self._warehouses) == 0:
      return ""
    headers = [
        TableHeader("Name", TableHeader.LEFT, isCode=True),
        TableHeader("Type", TableHeader.LEFT),
        TableHeader("Connected", TableHeader.LEFT),
        TableHeader("Deploy Status", TableHeader.LEFT),
    ]
    rows: List[List[str]] = []
    for w in self._warehouses:
      connectedAgo = timeago(w.createdAtMs)
      rows.append([w.displayName, str(w.type), connectedAgo, w.deployStatusPretty])
    return renderTemplate("table", headers=headers, rows=rows)


def list(mbApi: MbApi) -> WarehousesList:
  return WarehousesList(mbApi)
