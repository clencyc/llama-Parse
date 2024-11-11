from typing import Optional, Any, Union, Dict, cast, List, Generator, Tuple
from modelbit.error import UserFacingError
from modelbit.utils import toUrlBranch, dumpJson, inChunks, progressBar
from modelbit.api.api import makeRetry
import re, requests
from requests import ConnectionError
from requests.adapters import HTTPAdapter
import logging
import pandas, sys, math, threading
from concurrent.futures import ThreadPoolExecutor, Future

DefaultBatchSize = 3_000
DefaultBatchBytes = 20_000_000
DefaultConcurrency = 3
_session = requests.Session()
_session.mount('http://', HTTPAdapter(max_retries=makeRetry(backoffFactor=1)))
_session.mount('https://', HTTPAdapter(max_retries=makeRetry(backoffFactor=1)))

logger = logging.getLogger(__name__)


def _isMissingArg(value: Any) -> bool:
  return value is None or value == ""


def _isInvalidUrlArg(value: Any) -> bool:
  return type(value) is not str or re.fullmatch("[a-zA-Z0-9./_:+=-]+", value) is None


def _assertValid(value: Any, param: str, bonusMessage: Optional[str] = None) -> None:
  if _isMissingArg(value):
    raise UserFacingError(f"Missing '{param}' value. {bonusMessage or ''}".strip())
  elif _isInvalidUrlArg(value):
    raise UserFacingError(f"Invalid '{param}' value (Received '{value}'). {bonusMessage or ''}".strip())


def _assertValidData(data: Any) -> None:
  if type(data) is dict and "data" in data and len(cast(Dict[str, Any], data).keys()) == 1:
    raise UserFacingError(  # see https://gitlab.com/modelbit/modelbit/-/issues/1920
        "The 'data' parameter is double-nested. It should be data=... not data={data: ...} ")


def _assertValidResponseFormat(responseFormat: Optional[str]) -> None:
  if responseFormat is not None and responseFormat not in ["links"]:
    raise UserFacingError(f"Invalid response_format. Should be 'links' or None")


def _assertValidResponseWebhook(responseWebhook: Optional[str],
                                responseWebhookIgnoreTimeout: Optional[bool]) -> None:
  if responseWebhook is not None and not responseWebhook.startswith("https"):
    raise UserFacingError(f"Invalid response_webhook. It must be an https URL")
  if responseWebhookIgnoreTimeout is not None and responseWebhook is None:
    raise UserFacingError(
        f"The response_webhook parameter is required when using response_webhook_ignore_timeout.")
  if responseWebhookIgnoreTimeout is not None and type(responseWebhookIgnoreTimeout) is not bool:
    raise UserFacingError(f"The response_webhook_ignore_timeout parameter must be a boolean.")


def _headers(apiKey: Optional[str]) -> Dict[str, str]:
  base: Dict[str, str] = {"Content-Type": "application/json"}
  if apiKey is not None:
    base["Authorization"] = apiKey
  return base


def callDeployment(region: str,
                   workspace: Optional[str],
                   outpost: Optional[str],
                   branch: Optional[str],
                   deployment: str,
                   version: Union[str, int],
                   data: Any,
                   apiKey: Optional[str],
                   timeoutSeconds: Optional[int],
                   batchChunkSize: Optional[int] = DefaultBatchSize,
                   batchChunkBytes: Optional[int] = DefaultBatchBytes,
                   batchConcurrency: Optional[int] = DefaultConcurrency,
                   responseFormat: Optional[str] = None,
                   responseWebhook: Optional[str] = None,
                   responseWebhookIgnoreTimeout: Optional[bool] = None) -> Dict[str, Any]:

  _assertValid(region, "region")
  _assertValid(workspace, "workspace", "Supply the 'workspace' parameter or set the MB_WORKSPACE_NAME envvar")
  _assertValid(branch, "branch")
  branch = cast(str, branch)
  _assertValid(deployment, "deployment")
  _assertValidData(data)
  _assertValidResponseFormat(responseFormat)
  _assertValidResponseWebhook(responseWebhook=responseWebhook,
                              responseWebhookIgnoreTimeout=responseWebhookIgnoreTimeout)

  if _isMissingArg(version):
    raise UserFacingError(f"Missing 'version' value.")
  strVersion = str(version)
  _assertValid(strVersion, "version")

  if apiKey is not None:
    if type(apiKey) is not str:
      raise UserFacingError(f"Invalid API Key value.")
    _assertValid(apiKey, "api_key", "Supply the 'api_key' parameter or set the MB_API_KEY envvar")

  if timeoutSeconds is not None and (type(timeoutSeconds) is not int or timeoutSeconds <= 0):
    raise UserFacingError(f"Invalid 'timeout_seconds' value. It must be a positive integer.")

  if batchChunkSize is None:
    batchChunkSize = DefaultBatchSize
  elif type(batchChunkSize) is not int or batchChunkSize <= 0:
    raise UserFacingError(f"Invalid 'batch_size' value. It must be a positive integer.")

  if batchChunkBytes is None:
    batchChunkBytes = DefaultBatchBytes
  elif type(batchChunkBytes) is not int or batchChunkBytes <= 0:
    raise UserFacingError(f"Invalid 'batch_bytes' value. It must be a positive integer.")

  if batchConcurrency is None:
    batchConcurrency = DefaultConcurrency
  elif type(batchConcurrency) is not int or batchConcurrency <= 0:
    raise UserFacingError(f"Invalid 'batch_concurrency' value. It must be a positive integer.")

  regionSuffix = ".modelbit.com" if "modelbit.dev" not in region else ""
  maybeOutpost = f".{outpost}" if outpost else ""
  url = f"https://{workspace}{maybeOutpost}.{region}{regionSuffix}/v1/{deployment}/{toUrlBranch(branch)}/{strVersion}"
  if ":5000" in region:
    url = f"http://{workspace}.{region}/v1/{deployment}/{toUrlBranch(branch)}/{strVersion}"

  extraParams: Dict[str, Any] = {}
  if timeoutSeconds:
    extraParams["timeout_seconds"] = timeoutSeconds
  if responseFormat:
    extraParams["response_format"] = responseFormat
  if responseWebhook:
    extraParams["response_webhook"] = responseWebhook
    if responseWebhookIgnoreTimeout:
      extraParams["response_webhook_ignore_timeout"] = responseWebhookIgnoreTimeout

  rqTimeout = timeoutSeconds + 5 if timeoutSeconds else None
  convertedData = convertData(data)

  if canChunk(data):
    return makeRequestInChunks(convertedData=convertedData,
                               batchChunkSize=batchChunkSize,
                               batchChunkBytes=batchChunkBytes,
                               batchConcurrency=batchConcurrency,
                               url=url,
                               apiKey=apiKey,
                               extraParams=extraParams,
                               rqTimeout=rqTimeout)
  else:
    return makeRequest(url=url,
                       headers=_headers(apiKey),
                       data=convertedData,
                       extraParams=extraParams,
                       rqTimeout=rqTimeout)


def makeRequestInChunks(convertedData: List[Any], batchChunkSize: int, batchChunkBytes: int,
                        batchConcurrency: int, url: str, apiKey: Optional[str], extraParams: Dict[str, Any],
                        rqTimeout: Optional[int]) -> Dict[str, Any]:
  orderedFutures: List[Tuple[int, Future[Dict[str, Any]]]] = []
  allResults: List[Any] = []
  errorSignal = threading.Event()

  with ThreadPoolExecutor(max_workers=batchConcurrency) as executor:
    with progressBar(inputSize=len(convertedData),
                     desc=f"Getting inferences",
                     forceVisible=(len(convertedData) > batchChunkSize),
                     unit="it",
                     unitDivisor=1000,
                     barFormat="{l_bar}{bar}| {n_fmt}/{total_fmt} rows [{elapsed}<{remaining}]") as t:
      for chunkId, dataChunk in enumerate(
          breakIntoChunks(convertedData, maxRows=batchChunkSize, maxBytes=batchChunkBytes)):
        logger.info(f"SchedulingBatch id={chunkId} length={len(dataChunk)}")
        orderedFutures.append((chunkId,
                               executor.submit(makeRequest,
                                               url=url,
                                               headers=_headers(apiKey),
                                               data=dataChunk,
                                               extraParams=extraParams,
                                               rqTimeout=rqTimeout,
                                               errorSignal=errorSignal)))

        # Eagerly fetch results from results that complete during scheduling
        while len(orderedFutures) > 0 and orderedFutures[0][1].done():
          chunkFuture = orderedFutures.pop(0)
          result = chunkFuture[1].result()["data"]
          logger.info(f"BatchComplete early=true id={chunkFuture[0]} length={len(result)}")
          allResults += result
          t.update(len(result))

      # Get remaining results
      for chunkFuture in orderedFutures:
        result = chunkFuture[1].result()["data"]
        logger.info(f"BatchComplete id={chunkFuture[0]} length={len(result)}")
        allResults += result
        t.update(len(result))
  return {"data": allResults}


def canChunk(data: Any) -> bool:
  if type(data) is pandas.DataFrame:
    return True
  if type(data) is list:
    data = cast(List[Any], data)
    if len(data) > 0 and type(data[0]) is list:
      return True
  return False


def makeRequest(url: str,
                headers: Dict[str, str],
                data: List[Any],
                extraParams: Dict[str, Any],
                rqTimeout: Optional[int],
                errorSignal: Optional[threading.Event] = None) -> Dict[str, Any]:
  if errorSignal is not None and errorSignal.is_set():
    # Some other thread is throwing an exception
    logger.info("AbortingBatch reason=SiblingBatchError")
    return {}

  try:
    result = _session.post(url,
                           headers=headers,
                           data=dumpJson({
                               "data": data,
                               **extraParams,
                           }),
                           timeout=rqTimeout).json()
    if "error" in result:
      if errorSignal is not None:
        errorSignal.set()
      print(result["error"], file=sys.stderr)
      raise UserFacingError(result["error"].split("\n")[-1])
    return result
  except ConnectionError as err:
    logger.error(err)
    raise UserFacingError(f"Unable to connect to '{url}'.")


def convertData(data: Any) -> Any:
  if type(data) is pandas.DataFrame:
    splitDict = data.to_dict(  # type: ignore
        orient="split")  # see https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.to_dict.html
    records: List[Any] = []
    for rowIdx, dfIndex in enumerate(splitDict["index"]):
      dataRow: Dict[str, Any] = {}
      for cIdx, colName in enumerate(splitDict["columns"]):
        dataRow[colName] = splitDict["data"][rowIdx][cIdx]
      records.append([dfIndex, dataRow])
    return records
  return data


# yields chunks instead of processing all the chunks because chunking can take a while on very large item lists
def breakIntoChunks(items: List[Any], maxRows: int, maxBytes: int) -> Generator[List[Any], Any, None]:
  chunks = inChunks(items, maxRows)
  for chunk in chunks:
    if len(dumpJson(chunk)) < maxBytes:
      yield chunk
    else:
      if len(chunk) == 1:
        raise UserFacingError(f"Inference payload too large to process (max={maxBytes}): {chunk}")
      for subChunk in breakIntoChunks(items=chunk, maxRows=math.ceil(len(chunk) / 2), maxBytes=maxBytes):
        yield subChunk
