from typing import Any, List, Union

import numpy
import pandas
from modelbit.api import DatasetApi, DatasetDesc, MbApi
from modelbit.helpers import getCurrentBranch
from modelbit.utils import sizeOfFmt, timeago
from modelbit.ux import TableHeader, UserImage, renderTemplate, renderTextTable, TableType


class DatasetList:

  def __init__(self, api: MbApi):
    self._datasets: List[DatasetDesc] = DatasetApi(api).listDatasets(getCurrentBranch())
    self._iter_current = -1
    self._isAuthenticated = api.isAuthenticated()

  def __repr__(self) -> str:
    if not self._isAuthenticated:
      return ""
    return self._makeDatasetsTable(plainText=True)

  def _repr_html_(self) -> str:
    if not self._isAuthenticated:
      return ""
    return self._makeDatasetsTable()

  def __iter__(self) -> Any:
    return self

  def __next__(self) -> str:
    self._iter_current += 1
    if self._iter_current < len(self._datasets):
      return self._datasets[self._iter_current].name
    raise StopIteration

  def _makeDatasetsTable(self, plainText: bool = False) -> str:
    if len(self._datasets) == 0:
      return "There are no datasets to show."
    headers, rows = self._makeTable()
    if plainText:
      return renderTextTable(headers, rows)
    return renderTemplate("table", headers=headers, rows=rows)

  def _makeTable(self) -> TableType:
    headers = [
        TableHeader("Name", TableHeader.LEFT, isCode=True),
        TableHeader("Owner", TableHeader.CENTER),
        TableHeader("Data Refreshed", TableHeader.RIGHT),
        TableHeader("SQL Updated", TableHeader.RIGHT),
        TableHeader("Rows", TableHeader.RIGHT),
        TableHeader("Bytes", TableHeader.RIGHT),
    ]
    rows: List[List[Union[str, UserImage]]] = []
    for d in self._datasets:
      rows.append([
          d.name,
          UserImage(d.ownerInfo.imageUrl, d.ownerInfo.name),
          timeago(d.recentResultMs) if d.recentResultMs is not None else '',
          timeago(d.sqlModifiedAtMs) if d.sqlModifiedAtMs is not None else '',
          _fmt_num(d.numRows),
          sizeOfFmt(d.numBytes)
      ])
    return (headers, rows)


def list(api: MbApi) -> DatasetList:
  return DatasetList(api)


def convertDbNulls(df: pandas.DataFrame) -> None:
  df.replace(["\\N", "\\\\N"], numpy.nan, inplace=True)  # type: ignore


def _fmt_num(num: Union[int, Any]) -> str:
  if type(num) != int:
    return ""
  return format(num, ",")
