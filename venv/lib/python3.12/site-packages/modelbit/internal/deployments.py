from typing import Dict, List, Union

from modelbit.api import RuntimeApi, MbApi, RuntimeDesc
from modelbit.helpers import getCurrentBranch
from modelbit.utils import timeago
from modelbit.ux import TableHeader, UserImage, renderTemplate, renderTextTable, TableType


class DeploymentsList:

  def __init__(self, mbApi: MbApi):
    self._deployments: List[RuntimeDesc] = RuntimeApi(mbApi).listDeployments(getCurrentBranch())
    self._isAuthenticated = mbApi.isAuthenticated()

  def __repr__(self) -> str:
    if not self._isAuthenticated:
      return ""
    return self._makeDeploymentsTable(plainText=True)

  def _repr_html_(self) -> str:
    if not self._isAuthenticated:
      return ""
    return self._makeDeploymentsTable()

  def _makeDeploymentsTable(self, plainText: bool = False) -> str:
    if len(self._deployments) == 0:
      return "There are no deployments to show."
    headers, rows = self._makeTable()
    if plainText:
      return renderTextTable(headers, rows)
    return renderTemplate("table", headers=headers, rows=rows)

  def _makeTable(self) -> TableType:
    from collections import defaultdict
    deploymentsByName: Dict[str, List[RuntimeDesc]] = defaultdict(lambda: [])
    for d in self._deployments:
      deploymentsByName[d.name].append(d)

    headers = [
        TableHeader("Name", TableHeader.LEFT, isCode=True),
        TableHeader("Owner", TableHeader.CENTER),
        TableHeader("Version", TableHeader.RIGHT),
        TableHeader("Deployed", TableHeader.LEFT),
    ]
    rows: List[List[Union[str, UserImage]]] = []
    for dList in deploymentsByName.values():
      ld = dList[0]
      connectedAgo = timeago(ld.deployedAtMs)
      rows.append([ld.name, UserImage(ld.ownerInfo.imageUrl, ld.ownerInfo.name), ld.version, connectedAgo])
    return (headers, rows)


def list(mbApi: MbApi) -> DeploymentsList:
  return DeploymentsList(mbApi)
