import codecs
from datetime import datetime, timedelta
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple, Union, TextIO, cast
from tqdm import tqdm
from io import StringIO
from concurrent.futures import ThreadPoolExecutor

from modelbit.api import MbApi, BranchApi, RegistryApi
from modelbit.error import UserFacingError
from modelbit.helpers import getCurrentBranch
from modelbit.internal.auth import isAuthenticated as isAuthenticated
from modelbit.internal.describe import calcHash, describeObject, calcHashFromFile, fileSize, getFileType
from modelbit.internal.retry import retry
from modelbit.internal.runtime_objects import downloadRuntimeObject, downloadRuntimeObjectToFile, uploadRuntimeObject, uploadRuntimeObjectFromFile
from modelbit.internal.s3 import getS3FileBytes, getRuntimeObjectToBytes, getRuntimeObjectToFile, s3ObjectCachePath
from modelbit.internal.secure_storage import getSecureData
from modelbit.utils import inDeployment, maybePlural, toUrlBranch, tryPickle, tryUnpickle, dumpJson, getSerializerDesc, progressBar, inChunks
from modelbit.ux import printTemplate
from modelbit.keras_wrapper import KerasWrapper
from modelbit.internal import tracing

logger = logging.getLogger(__name__)

_reg_cache: Optional[Tuple[datetime, str]] = None
_obj_cache: Dict[str, Any] = {}
_cacheBranch: str = getCurrentBranch()

MaxJsonRequestSize = 5_000_000
DefaultUploadBatchSize = 300


def registryCacheTtl() -> timedelta:
  if inDeployment():
    return timedelta(seconds=60)
  return timedelta(seconds=10)


def set(api: MbApi,
        name: str,
        model: Any,
        metrics: Optional[Dict[str, Any]] = None,
        serializer: Optional[str] = None) -> None:
  BranchApi(api).raiseIfProtected()
  _assertSetModelFormat(name=name, model=model, metrics=metrics)
  _assertSerializer(serializer=serializer)
  set_many(api, models={name: model}, metrics={name: metrics}, serializer=serializer)


def set_many(api: MbApi,
             models: Dict[str, Any],
             metrics: Optional[Dict[str, Optional[Dict[str, Any]]]] = None,
             serializer: Optional[str] = None) -> None:
  BranchApi(api).raiseIfProtected()
  _assertSetModelsFormat(models=models, metrics=metrics)
  _assertSerializer(serializer=serializer)

  _uploadModelFromNotebook(api, models=models, metrics=metrics, serializer=serializer)
  resetCache()
  printTemplate(
      "message",
      None,
      msgText=f"Success: {len(models)} {maybePlural(len(models), 'model')} added to the registry.",
  )


def setFiles(api: MbApi, files: Dict[str, str], metrics: Optional[Dict[str, Any]] = None) -> None:
  BranchApi(api).raiseIfProtected()
  _assertSetModelsFilesFormat(files=files, metrics=metrics)

  _uploadModelFromNotebook(api=api, files=files, metrics=metrics)
  printTemplate(
      "message",
      None,
      msgText=f"Success: {len(files)} {maybePlural(len(files), 'model')} added to the registry.",
  )


def _uploadModelFromNotebook(api: MbApi,
                             models: Optional[Dict[str, Any]] = None,
                             metrics: Optional[Dict[str, Any]] = None,
                             files: Optional[Dict[str, Any]] = None,
                             batchSize: int = DefaultUploadBatchSize,
                             serializer: Optional[str] = None) -> None:
  uploadedObjects: Dict[str, Any] = {}
  uploadedObjectBatches = [uploadedObjects]
  outputStream: TextIO = StringIO() if os.getenv('MB_TXT_MODE') else sys.stdout
  readyMetrics: Dict[str, Any] = metrics if metrics else {}

  if models is None:
    if files is None:
      raise UserFacingError("At least one of models= or files= must be specified.")
    for name, path in files.items():
      uploadedObjects[name] = _uploadFile(api=api,
                                          name=name,
                                          fromFile=path,
                                          showLoader=True,
                                          metrics=readyMetrics.get(name))
  elif len(models) < 10:
    for name, obj in models.items():
      uploadedObjects[name] = _pickleAndUpload(api=api,
                                               name=name,
                                               obj=obj,
                                               showLoader=True,
                                               metrics=readyMetrics.get(name),
                                               serializer=serializer)
  else:
    for name, obj in tqdm(models.items(),
                          desc=f"Uploading {len(models)} models",
                          bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} models [{elapsed}<{remaining}]",
                          file=outputStream):
      if len(uploadedObjects) >= batchSize or len(json.dumps(uploadedObjects)) > MaxJsonRequestSize / 2:
        uploadedObjects = {}
        uploadedObjectBatches.append(uploadedObjects)
      uploadedObjects[name] = _pickleAndUpload(api=api,
                                               name=name,
                                               obj=obj,
                                               showLoader=False,
                                               metrics=readyMetrics.get(name),
                                               serializer=serializer)

  for uploadedObjects in tqdm(uploadedObjectBatches,
                              desc=f"Updating registry",
                              bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
                              file=outputStream):
    _assertSetModelsRequestSize(uploadedObjects)
    RegistryApi(api).storeContentHashAndMetadata(uploadedObjects)


def set_metrics(api: MbApi, name: str, metrics: Dict[str, Any], merge: bool = False) -> None:
  BranchApi(api).raiseIfProtected()
  _assertSetModelMetricsFormat({name: metrics}, [name])
  RegistryApi(api).updateMetadata(name, metrics, merge)
  printTemplate("metrics-updated", None, name=name)


def get(api: MbApi, name: str) -> Any:
  _assertGetFormat(name)
  resetCacheIfBranchChanged()

  if (inDeployment() and name in _obj_cache):
    obj = _obj_cache[name]
    _logModelUsed(name)
    return obj

  reg = _getRegistry(api)
  if reg is None:
    raise UserFacingError(f"Model not found: {name}")

  for line in reg.split("\n"):
    if line.startswith(f"{name}="):
      jsonStr = line[len(name) + 1:]
      hash, serializerPackage, fileType = _tryReadHash(jsonStr)
      if fileType:
        raise UserFacingError(
            f"The model '{name}' is a file-based model. Supply the file= parameter to save it as a file.")
      if hash:
        serializer = serializerPackage.split("==")[0] if type(serializerPackage) is str else None
        obj = _getObject(api=api, name=name, contentHash=hash, serializer=serializer)
        _logModelUsed(name=name)
        return obj
  raise UserFacingError(f"Model not found: {name}")


def getMany(api: MbApi, prefix: Optional[str], names: Optional[List[str]]) -> Dict[str, Any]:
  _assertGetManyFormat(prefix=prefix, names=names)

  if names is None:
    names = list_names(api, prefix)

  results: Dict[str, Any] = {}
  if len(names) == 0:
    return results
  _getRegistry(api)  # load registry once before threads start up

  def getOne(name: str) -> None:
    with tracing.trace(name, False):
      results[name] = get(api, name)

  with ThreadPoolExecutor(max_workers=4) as executor:
    for name in names:
      executor.submit(getOne, name)
    executor.shutdown(wait=True)
  return results


def getFiles(api: MbApi, files: Dict[str, str]) -> None:
  _assertGetManyFilesFormat(files=files)
  reg = _getRegistry(api)

  def getHash(name: str) -> str:
    if reg is None:
      raise UserFacingError(f"Model not found: {name}")

    for line in reg.split("\n"):
      if line.startswith(f"{name}="):
        jsonStr = line[len(name) + 1:]
        hash, _, _ = _tryReadHash(jsonStr)
        if hash:
          return hash
    raise UserFacingError(f"Model not found: {name}")

  def getOne(name: str, path: str, contentHash: str) -> None:
    with tracing.trace(name, False):
      _getAsFile(api=api, name=name, path=path, contentHash=contentHash)

  with ThreadPoolExecutor(max_workers=4) as executor:
    for name, path in files.items():
      executor.submit(getOne, name=name, path=path, contentHash=getHash(name))
    executor.shutdown(wait=True)


def getMetrics(api: MbApi, nameOrNames: Union[str, List[str]]) -> Optional[Dict[str, Any]]:
  if type(nameOrNames) is str:
    metrics = RegistryApi(api).fetchModelMetrics([nameOrNames])
    return metrics.get(nameOrNames, None)
  elif type(nameOrNames) is list:
    for n in nameOrNames:
      if type(n) is not str:
        raise UserFacingError(f"Model names must be strings. Found {n} which is a {type(n)}.")
      if n == "":
        raise UserFacingError(f"Model names cannot be empty strings.")
    if len(nameOrNames) == 0:
      raise UserFacingError(f"Supply at least one model name to fetch metrics.")
    return RegistryApi(api).fetchModelMetrics(nameOrNames)
  else:
    raise UserFacingError(f"Error getting metrics. Expecting str or List[str] but found {type(nameOrNames)}")


def _logModelUsed(name: str) -> None:
  if inDeployment():
    print(f'![mb:model]({name})')  # for parsing out of stdout later


def list_names(api: MbApi, prefix: Optional[str] = None) -> List[str]:
  _assertListFormat(prefix)

  reg = _getRegistry(api)
  if reg is None:
    return cast(List[str], [])

  if prefix is not None:
    return [line[0:line.index("=")] for line in reg.split("\n") if line.startswith(prefix)]
  else:
    return [line[0:line.index("=")] for line in reg.split("\n")]


def delete(api: MbApi, names: Union[str, List[str]]) -> None:
  BranchApi(api).raiseIfProtected()
  _assertDeleteFormat(names)
  if not isinstance(names, List):
    names = [names]

  chunkSize = 10_000
  with progressBar(inputSize=len(names),
                   desc=f"Deleting models",
                   forceVisible=(len(names) > chunkSize),
                   unit=' m',
                   unitDivisor=1000) as t:
    for nsg in inChunks(names, chunkSize):
      RegistryApi(api).delete(nsg)
      t.update(len(nsg))
  printTemplate(
      "message",
      None,
      msgText=f"Success: {len(names)} {maybePlural(len(names), 'model')} removed from the registry.",
  )


def _assertSetModelFormat(name: str, model: Any, metrics: Optional[Dict[str, Any]]) -> None:
  if type(name) is not str:
    raise UserFacingError(f"name= must be a string. It's currently a {type(name)}")
  if not name:
    raise UserFacingError(f"name= must not be empty.")
  if len(name) < 2:
    raise UserFacingError(f"Model names must be at least two characters.")
  if model is None:
    raise UserFacingError(f"model= must not be None.")
  _assertNotMainModuleClassInstance(model)
  _assertSetModelMetricsFormat({name: metrics}, [name])


def _assertSetModelsFormat(models: Dict[str, Any], metrics: Optional[Dict[str, Any]]) -> None:
  if type(models) is not dict:
    raise UserFacingError(f"models= must be a dictionary. It's currently a {type(models)}")
  if len(models) == 0:
    raise UserFacingError(f"The dict of models to add cannot be empty.")
  for k, v in models.items():
    if type(k) is not str:
      raise UserFacingError(f"Model keys must be strings. Found '{k}' which is a {type(v)}")
    if not k:
      raise UserFacingError(f"Model keys must not be empty.")
    if v is None:
      raise UserFacingError(f"Model values must not be None.")
    if len(k) < 2:
      raise UserFacingError(f"Model keys must be at least two characters.")
    _assertNotMainModuleClassInstance(v)
  _assertSetModelMetricsFormat(metrics, list(models.keys()))


def _assertSetModelsFilesFormat(files: Dict[str, str], metrics: Optional[Dict[str, Any]]) -> None:
  if type(files) is not dict:
    raise UserFacingError(f"files= must be a dictionary. It's currently a {type(files)}.")
  if len(files) == 0:
    raise UserFacingError(f"The dict of files to add cannot be empty.")
  for name, path in files.items():
    if type(name) is not str:
      raise UserFacingError(f"Model names must be strings. Found '{name}' which is a {type(name)}.")
    if not name:
      raise UserFacingError(f"Model names cannot be empty.")
    if type(path) is not str:
      raise UserFacingError(f"Model file paths must be strings. Found '{path}' which is a {type(path)}.")
    if not path:
      raise UserFacingError(f"Model file paths cannot be empty.")
    if not os.path.exists(path):
      raise UserFacingError(f"Model file not found: {path}")
  _assertSetModelMetricsFormat(metrics, list(files.keys()))


def _assertSetModelMetricsFormat(metrics: Optional[Dict[str, Optional[Dict[str, Any]]]],
                                 modelNames: List[str]) -> None:
  if metrics is None:
    return
  if type(metrics) is not dict:
    raise UserFacingError(f"Model metrics must be a dictionary of modelName -> metricsDict.")

  for modelName, metricsDict in metrics.items():
    if type(modelName) is not str:
      raise UserFacingError(f"Expecting a string model name as the key, but found {type(modelName)}")
    if metricsDict is None:
      continue
    if type(metricsDict) is not dict:
      raise UserFacingError(f"Expecting a dictionary for metric values, but found {type(metricsDict)}")
    if modelName not in modelNames:
      raise UserFacingError(
          f"Model metrics must be a dictionary of modelName -> metricsDict. There is no model named '{modelName}' in this update."
      )

    for k, v in metricsDict.items():
      if type(k) is not str:
        raise UserFacingError(f"Metric keys must be strings. Found '{k}' which is a {type(k)}")
      if len(k) == 0:
        raise UserFacingError(f"Metric keys cannot be empty strings")
      try:
        dumpJson(v)
      except Exception as err:
        raise UserFacingError(
            f"Metric values must be JSON-serializable. The value of '{k}' is {type(v)}. Error: {err}")


def _assertSerializer(serializer: Optional[str]) -> None:
  if serializer not in [None, "cloudpickle"]:
    raise UserFacingError("The 'serializer' value is invalid. It must either be None or 'cloudpickle'.")


def _assertSetModelsRequestSize(request: Dict[str, Any]) -> None:
  if len(dumpJson(request)) > MaxJsonRequestSize:
    raise UserFacingError("Request size exceeds maximum allowed (5MB). Add fewer models at a time.")


def _assertDeleteFormat(names: Union[str, List[str]]) -> None:
  if type(names) is str:
    if not names:
      raise UserFacingError(f"names= must not be empty.")
    return
  if type(names) is list:
    if not names:
      raise UserFacingError(f"names= must not be empty.")
    for n in names:
      if type(n) is not str:
        raise UserFacingError(f"Names must only contain strings. Found '{n}' which is a {type(n)}")
      if not n:
        raise UserFacingError(f"Names must not contain empty strings")
    return
  raise UserFacingError(f"names= must be a string or a list of strings. It's currently a {type(names)}")


def _assertGetFormat(name: str) -> None:
  if type(name) is not str:
    raise UserFacingError(f"name= must be a string. It's currently a {type(name)}")
  if not name:
    raise UserFacingError(f"name= must not be empty.")


def _assertGetManyFormat(prefix: Optional[str], names: Optional[List[str]]) -> None:
  if prefix and type(prefix) is not str and (names is None or type(names) is not list or len(names) == 0):
    raise UserFacingError(f"prefix= must be a string or names= must be a list of strings.")
  if prefix and names:
    raise UserFacingError(f"Only one of prefix= or names= can be supplied.")
  if prefix and type(prefix) is not str and not names:
    raise UserFacingError(f"prefix= must be a string. It's currently a {type(prefix)}.")
  if names and type(names) is not list:
    raise UserFacingError(f"names= must be a list of strings.")
  if names:
    for n in names:
      if type(n) is not str:
        raise UserFacingError(f"In the list of names one is not a string: {n}")


def _assertGetManyFilesFormat(files: Dict[str, str]) -> None:
  if type(files) is not dict:
    raise UserFacingError(f"files= must be a dictionary.")
  if len(files) == 0:
    raise UserFacingError(f"files= cannot be empty.")
  for name, path in files.items():
    if type(name) is not str:
      raise UserFacingError(
          f"Keys in the files dictionary are names of models and must be strings. Found: {name}: {type(name)}"
      )
    if not name:
      raise UserFacingError(f"Model names cannot be empty.")
    if type(path) is not str:
      raise UserFacingError(
          f"Values in the files dictionary are filepaths to store the model locally and must be strings. Found: {path}: {type(path)}"
      )
    if not path:
      raise UserFacingError(f"Model file paths cannot be empty.")


def _assertListFormat(prefix: Optional[str]) -> None:
  if prefix is None:
    return
  if type(prefix) is not str:
    raise UserFacingError(f"prefix= must be a string. It's currently a {type(prefix)}")
  if not prefix:
    raise UserFacingError(f"prefix= must not be empty.")


def _uploadFile(api: MbApi, fromFile: str, name: str, showLoader: bool,
                metrics: Optional[Dict[str, Any]]) -> Dict[str, Any]:
  contentHash = calcHashFromFile(fromFile)
  uploadRuntimeObjectFromFile(api=api,
                              fromFile=fromFile,
                              contentHash=contentHash,
                              uxDesc=name,
                              showLoader=showLoader)
  fileType = getFileType(fromFile)
  metadata: Dict[str, Any] = {
      "contentHash": contentHash,
      "metadata": {
          "size": fileSize(fromFile),
          "description": {
              "object": {  # used for UX, can be changed once we figure out better descriptions
                  "fileType": fileType
              }
          },
          "trainingJobId": os.environ.get("JOB_ID", None),
          "metrics": metrics,
          "fileType": fileType  # used for identifying as file in registry
      },
  }
  return metadata


def _pickleAndUpload(api: MbApi,
                     name: str,
                     obj: Any,
                     showLoader: bool,
                     metrics: Optional[Dict[str, Any]],
                     serializer: Optional[str] = None) -> Dict[str, Any]:
  objData = tryPickle(obj=_maybeWrap(obj), name=name, serializer=serializer)

  contentHash = calcHash(objData)
  description = describeObject(obj, 1)
  size = len(objData)
  uploadRuntimeObject(api, objData, contentHash, name, showLoader)
  metadata: Dict[str, Any] = {
      "contentHash": contentHash,
      "metadata": {
          "size": size,
          "description": description,
          "trainingJobId": os.environ.get("JOB_ID", None),
          "metrics": metrics,
          "serializer": getSerializerDesc(serializer)
      },
  }
  return metadata


def _getRegistry(api: MbApi) -> Optional[str]:
  global _reg_cache
  resetCacheIfBranchChanged()

  if _reg_cache:
    ts, regCache = _reg_cache
    if datetime.now() - ts < registryCacheTtl():
      return regCache
    else:
      _reg_cache = None

  reg = _getRegistryInDeployment() if inDeployment() else _getRegistryInNotebook(api)
  if reg:
    _reg_cache = datetime.now(), reg
  return reg


def _getRegistryInDeployment() -> Optional[str]:
  registryBytes = _getS3RegistryBytes()
  if registryBytes:
    reg = registryBytes.decode("utf-8")
    return reg
  return None


@retry(4, logger)
def _getS3RegistryBytes() -> bytes:
  branch = getCurrentBranch()
  regPath = f"registry_by_branch/{toUrlBranch(branch)}/registry.txt.zstd.enc"
  return getS3FileBytes(regPath)


@retry(4, logger)
def _getRegistryInNotebook(api: MbApi) -> Optional[str]:
  dri = RegistryApi(api).getRegistryDownloadInfo()
  if dri:
    return codecs.decode(getSecureData(dri=dri, desc="model registry"))
  return None


def _tryReadHash(jsonStr: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:  # [hash, serializer]
  try:
    jRes = json.loads(jsonStr)
    hash = jRes.get("id", None)
    serializerPackage = jRes.get("serializer", None)
    fileType = jRes.get("fileType", None)
    if type(hash) is str:
      return (hash, serializerPackage, fileType)
    return (None, None, None)
  except json.JSONDecodeError:
    raise UserFacingError("Unable to find model in registry.")


def _getObject(api: MbApi, name: str, contentHash: str, serializer: Optional[str] = None) -> Any:
  resetCacheIfBranchChanged()

  if name in _obj_cache:
    return _obj_cache[name]

  if inDeployment():
    obj = _getObjectInDeployment(name=name, contentHash=contentHash, serializer=serializer)
  else:
    obj = _getObjectInNotebook(api=api, name=name, contentHash=contentHash, serializer=serializer)
  _obj_cache[name] = obj
  return obj


def _getObjectInDeployment(name: str, contentHash: str, serializer: Optional[str] = None) -> Any:
  runtimeObjBytes = _getS3ObjectBytes(contentHash)
  assert runtimeObjBytes is not None
  try:
    return _maybeUnwrap(tryUnpickle(obj=runtimeObjBytes, name=name, serializer=serializer))
  except UserFacingError as e:
    raise e
  except ModuleNotFoundError as err:
    raise UserFacingError(f"Module missing from environment: {str(err.name)}")
  except Exception as err:
    raise UserFacingError(f"{err.__class__.__name__} while loading model {name}: {err}")


@retry(4, logger)
def _getS3ObjectBytes(contentHash: str) -> bytes:
  return getRuntimeObjectToBytes(contentHash=contentHash)


def _getObjectInNotebook(api: MbApi, name: str, contentHash: str, serializer: Optional[str] = None) -> Any:
  try:
    obj = downloadRuntimeObject(api=api, contentHash=contentHash, desc=name)
    return _maybeUnwrap(tryUnpickle(obj=obj, name=name, serializer=serializer))
  except UserFacingError as e:
    raise e
  except ModuleNotFoundError as err:
    raise UserFacingError(f"Module missing from environment: {str(err.name)}")
  except Exception as err:
    raise UserFacingError(f"{err.__class__.__name__} while loading model {name}: {err}")


@retry(4, logger)
def _getAsFile(api: MbApi, name: str, path: str, contentHash: str) -> None:
  if os.path.exists(path) and calcHashFromFile(path) == contentHash:
    logger.info(f"Skipping fetch, file already exists: {path}")
    _logModelUsed(name)
    return

  if inDeployment():
    getRuntimeObjectToFile(contentHash=contentHash)
    if os.path.exists(path):
      os.unlink(path)
    os.symlink(s3ObjectCachePath(contentHash), path)
  else:
    downloadRuntimeObjectToFile(api=api, contentHash=contentHash, desc=name, toFile=path)
  _logModelUsed(name)


def _maybeWrap(model: Any) -> Union[KerasWrapper, Any]:
  if KerasWrapper.isKerasModel(model):
    return KerasWrapper(model)
  return model


def _maybeUnwrap(obj: Any) -> Any:
  if isinstance(obj, KerasWrapper):
    return obj.getModel()
  return obj


def _assertNotMainModuleClassInstance(obj: Any) -> None:
  if isinstance(obj, object) and hasattr(obj, "__class__") and hasattr(
      obj.__class__, "__module__") and obj.__class__.__module__ == "__main__":
    raise UserFacingError(
        "Instances of classes defined in this file cannot be added to the registry. You can move the class definition to a new file and use it with the registry."
    )


def resetCache() -> None:
  global _reg_cache, _obj_cache
  _reg_cache = None
  _obj_cache = {}


def resetCacheIfBranchChanged() -> None:
  global _cacheBranch
  if _cacheBranch != getCurrentBranch():
    _cacheBranch = getCurrentBranch()
    resetCache()
