from typing import Optional, Dict, List, Any, Union
import json, pandas, numpy, tempfile, sqlite3, logging, time, codecs, os, re
from modelbit.api import MbApi, DatasetApi
from modelbit.error import UserFacingError
from modelbit.utils import inDeployment, toUrlBranch, inNotebook, progressBar
from modelbit.internal.secure_storage import getSecureData, DownloadableObjectInfo
from modelbit.internal.s3 import getS3FileBytes
from modelbit.internal.retry import retry
from modelbit.internal.named_lock import namedLock

logger = logging.getLogger(__name__)

FilterType = Optional[Dict[str, Dict[str, List[Any]]]]
AcceptedFilterType = Optional[Dict[str, Any]]


@retry(4, logger)
def _wrappedGetS3FileBytes(path: str) -> bytes:
  return getS3FileBytes(path)


@retry(4, logger)
def _wrappedGetSecureData(dri: DownloadableObjectInfo, desc: str) -> Union[bytes, memoryview]:
  return getSecureData(dri=dri, desc=desc)


def downloadDecryptData(mbApi: MbApi, path: str, desc: str) -> Union[bytes, memoryview, None]:
  if inDeployment():
    return _wrappedGetS3FileBytes(path)
  else:
    return _downloadFromWeb(mbApi, path=path, desc=desc)


def _downloadFromWeb(mbApi: MbApi, path: str, desc: str) -> Union[bytes, memoryview, None]:
  dri = DatasetApi(mbApi).getDatasetPartDownloadInfo(path)
  if dri is None:
    raise UserFacingError(f"Failed finding download info for dataset part: {path}")
  return _wrappedGetSecureData(dri, desc)


isoDateMatcher = re.compile(r'(\d{4,}-\d\d-\d\d)T(\d\d:\d\d:\d\d)')


class Shard:

  def __init__(self, mbApi: MbApi, params: Dict[str, Any], dsName: str, numShards: int):
    self.mbApi = mbApi
    self.dsName = dsName
    self.numShards = numShards
    self.shardId = params["shardId"]
    self.shardPath = params["shardPath"]
    self.shardColumn = params["shardColumn"]
    self.minValue = isoDateMatcher.sub(r'\1 \2', params["minValue"]) if type(
        params["minValue"]) is str else params["minValue"]
    self.maxValue = isoDateMatcher.sub(r'\1 \2', params["maxValue"]) if type(
        params["maxValue"]) is str else params["maxValue"]
    self.numBytes: int = params["numBytes"]
    self.numRows = params["numRows"]
    self.featureDbFilePath = os.path.join(tempfile.gettempdir(), os.urandom(10).hex())
    self.completeDf: Optional[pandas.DataFrame] = None
    self.dbConn: Optional[sqlite3.Connection] = None

  def __del__(self) -> None:
    if self.dbConn is not None:
      self.dbConn.close()
    if os.path.exists(self.featureDbFilePath):
      os.unlink(self.featureDbFilePath)

  def getDbConn(self) -> sqlite3.Connection:
    if self.dbConn is None:
      shardData = downloadDecryptData(self.mbApi, self.shardPath,
                                      f"{self.dsName} (part {self.shardId} of {self.numShards})")
      if shardData is None:
        raise Exception(f"FailedGettingShard path={self.shardPath}")
      with open(self.featureDbFilePath, "wb") as f:
        f.write(shardData)
      self.dbConn = sqlite3.connect(self.featureDbFilePath, check_same_thread=False)
    return self.dbConn

  def filterToDf(self, filters: FilterType) -> Optional[pandas.DataFrame]:
    if filters is None:
      return self.getCompleteDf()

    if self.canSkip(filters):
      return None

    filterGroups: List[str] = []
    filterParams: List[Any] = []
    for filterCol, filterOption in filters.items():
      for filterOp, filterValues in filterOption.items():
        filterGroup: List[str] = []
        for val in filterValues:
          if filterOp == 'lt':
            filterGroup.append(f"`{filterCol}` < ?")
          elif filterOp == 'lte':
            filterGroup.append(f"`{filterCol}` <= ?")
          elif filterOp == 'gt':
            filterGroup.append(f"`{filterCol}` > ?")
          elif filterOp == 'gte':
            filterGroup.append(f"`{filterCol}` >= ?")
          else:
            filterGroup.append(f"`{filterCol}` = ?")
          filterParams.append(val)
        filterGroups.append(f'({" or ".join(filterGroup)})')
    sql = f"select * from df where {' and '.join(filterGroups)}"
    try:
      df = pandas.read_sql_query(sql=sql, params=filterParams, con=self.getDbConn())  # type: ignore
      self.convertDbNulls(df)
      return None if len(df) == 0 else df
    except Exception as err:
      if "no such column: " in str(err):
        raise UserFacingError(f"Invalid filter column specified: {str(err).split('no such column: ')[-1]}")
      raise err

  def getCompleteDf(self) -> pandas.DataFrame:
    if self.completeDf is None:
      df = pandas.read_sql_query(sql="select * from df", con=self.getDbConn())  # type: ignore
      self.convertDbNulls(df)
      self.completeDf = df
    return self.completeDf

  def canSkip(self, filters: FilterType) -> bool:
    if filters is None or self.minValue is None or self.maxValue is None:
      return False

    colNameCaseMap: Dict[str, str] = {}
    for fName in filters.keys():
      colNameCaseMap[fName.lower()] = fName
    if self.shardColumn.lower() not in colNameCaseMap:
      return False

    filterShardColCasedToFilters = colNameCaseMap[self.shardColumn.lower()]
    filterOption = filters[filterShardColCasedToFilters]
    ltValues = filterOption.get('lt')
    if ltValues is not None and all(not lt(self.minValue, val) for val in ltValues):
      return True
    lteValues = filterOption.get('lte')
    if lteValues is not None and all(not lte(self.minValue, val) for val in lteValues):
      return True
    gtValues = filterOption.get('gt')
    if gtValues is not None and all(not lt(val, self.maxValue) for val in gtValues):
      return True
    gteValues = filterOption.get('gte')
    if gteValues is not None and all(not lte(val, self.maxValue) for val in gteValues):
      return True
    eqFilterValues = filterOption.get('eq')
    if eqFilterValues is not None and all(
        not between(val, self.minValue, self.maxValue) for val in eqFilterValues):
      return True
    return False

  def convertDbNulls(self, df: pandas.DataFrame) -> None:
    df.replace(["\\N", "\\\\N"], numpy.nan, inplace=True)  # type: ignore


def between(filterVal: Any, minValue: Any, maxValue: Any) -> bool:
  return lte(minValue, filterVal) and lte(filterVal, maxValue)


def lt(a: Any, b: Any) -> bool:
  try:
    return a < b
  except TypeError as err:
    if "'<'" in str(err):
      comparison = " < ".join([
          f"{type(a).__name__}({a})",
          f"{type(b).__name__}({b})",
      ])
      raise UserFacingError(f"Invalid comparison when searching for feature, {comparison}. Error: {err}")
    raise err


def lte(a: Any, b: Any) -> bool:
  try:
    return a <= b
  except TypeError as err:
    if "<=" in str(err):
      comparison = " <= ".join([
          f"{type(a).__name__}({a})",
          f"{type(b).__name__}({b})",
      ])
      raise UserFacingError(f"Invalid comparison when searching for feature, {comparison}. Error: {err}")
    raise err


class FeatureStore:

  ManifestTimeoutSeconds = 10 if inNotebook() else 5 * 60

  def __init__(self, mbApi: MbApi, branch: str, dsName: str):
    self.mbApi = mbApi
    self.branch = branch
    self.dsName = dsName
    self.manifestPath: str = self.getManifestPath()
    self.manifestBytes: Union[bytes, memoryview] = b""
    self.shards: List[Shard] = []
    self.manifestExpiresAt: float = 0

  def getManifestPath(self) -> str:
    return f'datasets/{self.dsName}/{toUrlBranch(self.branch)}.manifest'

  def maybeRefreshManifest(self) -> None:
    if len(self.manifestBytes) > 0 and time.time() < self.manifestExpiresAt:
      return
    newManifestBytes: Union[bytes, memoryview, None] = None
    notFoundError = UserFacingError(f"Dataset not found: {self.branch}/{self.dsName}")
    try:
      newManifestBytes = downloadDecryptData(self.mbApi, self.manifestPath, f"{self.dsName} (index)")
    except UserFacingError as err:
      raise err
    except Exception as err:
      # if we have a manifest and cannot refresh for some reason, just continue
      if len(self.manifestBytes) > 0:
        self.manifestExpiresAt = time.time() + self.ManifestTimeoutSeconds
        return
      raise UserFacingError(f"Unable to fetch dataset {self.branch}/{self.dsName}: {err}")
    if newManifestBytes is None:
      raise notFoundError
    if newManifestBytes != self.manifestBytes:
      newManifestData: Dict[str, Any] = json.loads(codecs.decode(newManifestBytes))
      shardInfo: List[Dict[str, Any]] = newManifestData["shards"]
      self.shards = [
          Shard(self.mbApi, params=d, dsName=self.dsName, numShards=len(shardInfo)) for d in shardInfo
      ]
      self.manifestBytes = newManifestBytes
    self.manifestExpiresAt = time.time() + self.ManifestTimeoutSeconds

  def totalShardsSize(self) -> int:
    total = 0
    for s in self.shards:
      total += s.numBytes
    return total

  def getDataFrame(self, filters: FilterType) -> pandas.DataFrame:
    self.maybeRefreshManifest()
    with progressBar(inputSize=self.totalShardsSize(), desc=f"Reading '{self.dsName}'") as t:
      shardResults: List[pandas.DataFrame] = []
      for idx, s in enumerate(self.shards):
        df = self.getShardDataFrame(filters, s, idx)
        if df is not None:
          shardResults.append(df)
        t.update(s.numBytes)
    if len(shardResults) == 0:
      firstDf = self.shards[0].getCompleteDf()
      assert firstDf is not None
      return firstDf.head(0)  # return empty df, not None, when filters match nothing
    return pandas.concat(shardResults)  #type: ignore

  def getShardDataFrame(self, filters: FilterType, shard: Shard, idx: int) -> Optional[pandas.DataFrame]:
    try:
      return shard.filterToDf(filters)
    except sqlite3.DatabaseError:  # retry once in case of transient error with the sqlite db
      self.shards[idx] = self.newShardFromManifestData(idx)
      return self.shards[idx].filterToDf(filters)

  def newShardFromManifestData(self, idx: int) -> Shard:
    manifestData = json.loads(codecs.decode(self.manifestBytes))
    shardInfo: List[Dict[str, Any]] = manifestData["shards"]
    return Shard(self.mbApi, params=shardInfo[idx], dsName=self.dsName, numShards=len(shardInfo))


class FeatureStoreManager:

  def __init__(self, mbApi: MbApi):
    self.mbApi = mbApi
    self.featureStores: Dict[str, FeatureStore] = {}

  def getFeatureStore(self, branch: str, dsName: str) -> FeatureStore:
    lookupKey = f"{branch}/${dsName}"
    if lookupKey not in self.featureStores:
      self.featureStores[lookupKey] = FeatureStore(mbApi=self.mbApi, branch=branch, dsName=dsName)
    return self.featureStores[lookupKey]


_featureStoreManager: Optional[FeatureStoreManager] = None


def _getFeatureStoreManager(mbApi: MbApi) -> FeatureStoreManager:
  global _featureStoreManager
  if _featureStoreManager is None:
    _featureStoreManager = FeatureStoreManager(mbApi)
  return _featureStoreManager


filterOpsAndAliases = {"gt": [">"], "gte": [">="], "lt": ["<"], "lte": ["<="], "eq": ["=", "=="]}
aliasToFilterOp = dict([(a, k) for k, v in filterOpsAndAliases.items() for a in v])
allFilterOps = [a for a in aliasToFilterOp.keys()] + [o for o in filterOpsAndAliases.keys()]


def assertFilterFormatAndNormalize(filters: AcceptedFilterType) -> None:
  if filters is None:
    return
  if type(filters) is not dict:
    raise UserFacingError(f"filters= must be None or a dictionary. It's currently a {type(filters)}")
  for k, f in filters.items():
    if type(k) is not str:
      raise UserFacingError(f"Filter keys must be strings. Found '{k}' which is a {type(f)}")
    if type(f) is not dict:
      f = filters[k] = {"eq": f}

    for op, v in f.items():  # type: ignore
      if type(op) is not str or op not in allFilterOps:  # type: ignore
        raise UserFacingError(f"Filter operator must be one of {', '.join(allFilterOps)}. Found '{op}'")
      if type(v) is not list:  # type: ignore
        v = f[op] = [v]  # type: ignore

    for a, op in aliasToFilterOp.items():
      if a in f:
        f[op] = list(set(f.get(op, []) + f[a]))  # type: ignore
        del f[a]


def getDataFrame(mbApi: MbApi,
                 branch: str,
                 dsName: str,
                 filters: AcceptedFilterType = None) -> pandas.DataFrame:
  if not inDeployment() and not mbApi.isAuthenticated():
    raise UserFacingError("Your session is not authenticated.")
  assertFilterFormatAndNormalize(filters)
  with namedLock("modelbit.featureStoreManager"):
    manager = _getFeatureStoreManager(mbApi)
  with namedLock(f"dataset.{branch}/{dsName}"):
    featureStore = manager.getFeatureStore(branch=branch, dsName=dsName)
    return featureStore.getDataFrame(filters)
