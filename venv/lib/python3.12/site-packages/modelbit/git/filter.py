import logging
import os
from typing import Union, Tuple

from modelbit.api import MbApi
from modelbit.internal.cache import stubCacheFilePath
from modelbit.internal.describe import shouldUploadFile, calcHash
from modelbit.internal.file_stubs import contentHashFromYaml, sizeFromYaml, isSharedFromYaml
from modelbit.internal.runtime_objects import describeAndUploadRuntimeObject, downloadRuntimeObject, downloadRuntimeObjectToFile

logger = logging.getLogger(__name__)


class GitFilter:

  def __init__(self, workspaceId: str, mbApi: MbApi):
    self.workspaceId = workspaceId
    self.api = mbApi

  def clean(self, filepath: str, content: bytes, skipCache: bool = False) -> bytes:
    if not shouldUploadFile(filepath, content):
      logger.info(f"Ignoring {filepath}")
      if content:
        return content
      return b''
    contentHash = calcHash(content)
    logger.info(f"Cleaning {filepath} hash={contentHash}")
    cacheFilepath = None
    if not skipCache:
      cacheFilepath = stubCacheFilePath(self.workspaceId, contentHash)
      if os.path.exists(cacheFilepath):  # Try cache
        try:
          with open(cacheFilepath, "rb") as f:
            yamlContent = f.read()
            if contentHashFromYaml(yamlContent) == contentHash:
              return yamlContent
        except Exception as e:
          logger.info("Failed to read from cache", exc_info=e)

    yamlContent = describeAndUploadRuntimeObject(self.api, None, content, filepath).encode('utf-8')
    if not skipCache and cacheFilepath is not None:
      with open(cacheFilepath, "wb") as f:
        f.write(yamlContent)
    return yamlContent

  def smudge(self, filepath: str, content: bytes) -> Union[bytes, memoryview]:
    contentHash, isShared = self.getContentHashInfo(filepath, content)
    if not contentHash:
      return content
    logger.info(f"Smudging {filepath} hash={contentHash}")
    return downloadRuntimeObject(api=self.api,
                                 contentHash=contentHash,
                                 isShared=bool(isShared),
                                 desc=filepath)

  def smudgeToFile(self, filepath: str, content: bytes, toFile: str) -> None:
    contentHash, isShared = self.getContentHashInfo(filepath, content)
    if not contentHash:
      with open(toFile, "wb") as f:
        f.write(content)
      return None

    logger.info(f"Smudging {filepath} hash={contentHash} toFile={toFile}")
    return downloadRuntimeObjectToFile(api=self.api,
                                       contentHash=contentHash,
                                       isShared=bool(isShared),
                                       desc=filepath,
                                       toFile=toFile)

  def getContentHashInfo(self, filepath: str, content: bytes) -> Union[Tuple[str, bool], Tuple[None, None]]:
    try:
      contentHash = contentHashFromYaml(content)
      isShared = isSharedFromYaml(content)
    except Exception:
      logger.info(f"SmudgeReadError {filepath}")
      return (None, None)
    if contentHash is None:
      return (None, None)
    # Store in cache
    # Otherwise diffs trigger if the locally environment differs
    cacheFilepath = stubCacheFilePath(self.workspaceId, contentHash)
    with open(cacheFilepath, "wb") as f:
      f.write(content)
    return (contentHash, isShared)

  def smallEnoughToDelaySmudge(self, content: bytes) -> bool:
    size = sizeFromYaml(content)
    return size is not None and size > 0 and size < 10_000_000
