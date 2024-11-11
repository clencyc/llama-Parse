import logging
import os
from typing import Any, Dict, IO

from .cache import objectCacheFilePath
from .encryption import decryptAndValidateToBytes, decryptAndValidateToFile, encryptDataFromBytes, encryptDataFromFile
from modelbit.utils import storeFileOnSuccess
from abc import ABCMeta, abstractmethod
from modelbit.internal.describe import fileSize
from modelbit.ux import SHELL_FORMAT_FUNCS as SFF
from modelbit.utils import progressBar

logger = logging.getLogger(__name__)

defaultRequestTimeout = 10
readBlockSize = 16 * 1024 * 1024  # 16MB


class UploadableObjectInfo:

  def __init__(self, data: Dict[str, Any]):
    self.workspaceId: str = data["workspaceId"]
    self.bucket: str = data["bucket"]
    self.s3Key: str = data["s3Key"]
    self.awsCreds: Dict[str, str] = data["awsCreds"]
    self.metadata: Dict[str, str] = data["metadata"]
    self.fileKey64: str = data["fileKey64"]
    self.fileIv64: str = data["fileIv64"]
    self.objectExists: bool = data["objectExists"]


class DownloadableObjectInfo(metaclass=ABCMeta):

  def __init__(self, data: Dict[str, Any]):
    self.workspaceId: str = data["workspaceId"]
    self.signedDataUrl: str = data["signedDataUrl"]
    self.key64: str = data["key64"]
    self.iv64: str = data["iv64"]

  @abstractmethod
  def cachekey(self) -> str:
    raise Exception("NYI")


def putSecureData(uploadInfo: UploadableObjectInfo, obj: bytes, desc: str, showLoader: bool,
                  encFileName: str) -> None:
  if uploadInfo.objectExists:
    return
  encryptDataFromBytes(inData=obj,
                       outFile=encFileName,
                       key64=uploadInfo.fileKey64,
                       iv64=uploadInfo.fileIv64,
                       desc=desc)
  with open(encFileName, "rb") as encFileReader:
    _uploadFile(uploadInfo,
                body=encFileReader,
                bodySize=fileSize(encFileName),
                desc=desc,
                showLoader=showLoader)


def putSecureDataFromFile(uploadInfo: UploadableObjectInfo, fromFile: str, desc: str, showLoader: bool,
                          encFileName: str) -> None:
  if uploadInfo.objectExists:
    return
  encryptDataFromFile(inFile=fromFile,
                      outFile=encFileName,
                      key64=uploadInfo.fileKey64,
                      iv64=uploadInfo.fileIv64,
                      desc=desc)
  with open(encFileName, "rb") as encFileReader:
    _uploadFile(uploadInfo,
                body=encFileReader,
                bodySize=fileSize(encFileName),
                desc=desc,
                showLoader=showLoader)


def _assertDri(dri: DownloadableObjectInfo) -> None:
  if not dri:
    raise Exception("Download info missing from API response.")


def getSecureData(dri: DownloadableObjectInfo, desc: str, isShared: bool = False) -> memoryview:
  _assertDri(dri)
  filepath = objectCacheFilePath(workspaceId=dri.workspaceId, contentHash=dri.cachekey(), isShared=isShared)
  expectedHash = getattr(dri, "contentHash") if hasattr(dri, "contentHash") else None

  if os.path.exists(filepath):  # Try cache
    try:
      return decryptAndValidateToBytes(filepath,
                                       key64=dri.key64,
                                       iv64=dri.iv64,
                                       expectedHash=expectedHash,
                                       desc=desc,
                                       isEncrypted=not isShared)
    except Exception as e:
      logger.info("Failed to read from cache", exc_info=e)

  _downloadFile(dri, filepath, desc)
  return decryptAndValidateToBytes(filepath,
                                   key64=dri.key64,
                                   iv64=dri.iv64,
                                   expectedHash=expectedHash,
                                   desc=desc,
                                   isEncrypted=not isShared)


def getSecureDataToFile(dri: DownloadableObjectInfo, desc: str, toFile: str, isShared: bool) -> None:
  _assertDri(dri)
  filepath = objectCacheFilePath(workspaceId=dri.workspaceId, contentHash=dri.cachekey(), isShared=isShared)
  expectedHash = getattr(dri, "contentHash") if hasattr(dri, "contentHash") else None

  if os.path.exists(filepath):  # Try cache
    try:
      return decryptAndValidateToFile(encFilePath=filepath,
                                      key64=dri.key64,
                                      iv64=dri.iv64,
                                      expectedHash=expectedHash,
                                      toFile=toFile,
                                      desc=desc,
                                      isEncrypted=not isShared)
    except Exception as e:
      logger.info("Failed to read from cache", exc_info=e)

  _downloadFile(dri, filepath, desc)
  return decryptAndValidateToFile(encFilePath=filepath,
                                  key64=dri.key64,
                                  iv64=dri.iv64,
                                  expectedHash=expectedHash,
                                  toFile=toFile,
                                  desc=desc,
                                  isEncrypted=not isShared)


def _uploadFile(uploadInfo: UploadableObjectInfo, body: IO[bytes], bodySize: int, desc: str,
                showLoader: bool) -> None:
  import boto3
  s3Client = boto3.client('s3', **uploadInfo.awsCreds)  # type: ignore
  with body as b, progressBar(inputSize=bodySize, desc=(SFF["yellow"]("Uploading") + f" '{desc}'")) as t:
    s3Client.upload_fileobj(  # type: ignore
        b,
        uploadInfo.bucket,
        uploadInfo.s3Key,
        ExtraArgs={"Metadata": uploadInfo.metadata},
        Callback=lambda bytes_transferred: t.update(bytes_transferred))  # type: ignore


def _downloadFile(dri: DownloadableObjectInfo, filepath: str, desc: str) -> None:
  import requests
  logger.info(f"Downloading to {filepath}")
  resp = requests.get(dri.signedDataUrl, stream=True, timeout=defaultRequestTimeout)
  total = int(resp.headers.get('content-length', 0))
  with storeFileOnSuccess(finalPath=filepath) as tmpPath:
    with open(tmpPath, "wb") as f, progressBar(inputSize=total,
                                               desc=(SFF["yellow"]("Downloading") + f" '{desc}'")) as t:
      for data in resp.iter_content(chunk_size=32 * 1024):
        size = f.write(data)
        t.update(size)
