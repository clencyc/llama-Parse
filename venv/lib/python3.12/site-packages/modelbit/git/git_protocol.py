import logging
import sys, os
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import List, NoReturn, Tuple, Iterator, Dict, Set, Union, Generator, Any
import tempfile
from contextlib import contextmanager
from .filter import GitFilter
from modelbit.utils import tempFilePath, getStatusOutputStream, progressBar, sizeOfFmt
from modelbit.ux import SHELL_FORMAT_FUNCS as SFF

logger = logging.getLogger(__name__)
FLUSH_PACKET = b"0000"


def toPkts(s: Union[bytes, memoryview]) -> Iterator[bytes]:
  size = len(s)
  offset = 0
  limit = min(size, 65516)
  while offset < size:
    yield b"%04x%s" % (limit + 4, s[offset:offset + limit])
    offset += limit
    limit = min(size - offset, 65516)


GIT_FILTER_THREADS = 10
MAX_FILE_DOWNLOAD_WAIT_SEC = 3600


class GitProtocolBase:
  """ Implements git-protocol. To aid debugging export GIT_TRACE_PACKET=1
      Roughly, a packet-based protocol. Each packet starts with 4 hex digits specifying
      the length.
      https://github.com/git/git/blob/master/Documentation/gitprotocol-common.txt
  """

  def __init__(self) -> None:
    self.inStream = sys.stdin.buffer
    self.outStream = sys.stdout.buffer

  def write(self, b: Union[bytes, memoryview]) -> None:
    if b is FLUSH_PACKET:
      self.outStream.write(b)
    else:
      for pkt in toPkts(b):
        self.outStream.write(pkt)
    self.outStream.flush()

  def readKVData(self) -> List[Tuple[str, str]]:
    data: List[Tuple[str, str]] = []
    while True:
      sizeHex = self.inStream.read(4)
      if len(sizeHex) == 0:
        raise EOFError
      size = int(sizeHex, 16)
      if size == 0:
        break
      pkt = self.inStream.read(size - 4)
      if b"=" in pkt:
        (k, v) = parseKv(pkt)
        data.append((k, v))
    return data

  def readBinaryData(self) -> bytes:
    with tempfile.NamedTemporaryFile() as tf:
      while True:
        size = int(self.inStream.read(4), 16)
        if size == 0:
          break
        tf.write(self.inStream.read(size - 4))
      tf.seek(0)
      return tf.read()


class GitProtocol(GitProtocolBase):

  def __init__(self, gitFilter: GitFilter):
    super().__init__()
    self.gitFilter = gitFilter
    self.executor = ThreadPoolExecutor(max_workers=GIT_FILTER_THREADS)
    self.smudgeFutures: Set[Future[Tuple[str, Union[bytes, memoryview]]]] = set()
    self.smudgesAwaitingPickup: Dict[str, Union[bytes, memoryview]] = dict()

  def versionHandshake(self) -> None:
    [version] = self.readKVData()  # type: ignore
    # TODO: Process handshake
    self.write(b"git-filter-server\n")
    self.write(b"version=2\n")
    self.write(FLUSH_PACKET)

  def capabilitiesHandshake(self) -> None:
    caps = self.readKVData()  # type: ignore
    # TODO: Process capabilities
    self.write(b"capability=clean\n")
    self.write(b"capability=smudge\n")
    self.write(b"capability=delay\n")
    self.write(FLUSH_PACKET)

  def readCommand(self) -> Tuple[str, str, bytes, bool]:
    hdr = dict(self.readKVData())
    command = hdr["command"].strip()
    pathname = hdr.get("pathname", "").strip()
    candelay = hdr.get("can-delay", "0").strip() == "1"
    content = self.readBinaryData() if command in ["smudge", "clean"] else b''
    return (command, pathname, content, candelay)

  def writeSuccess(self, content: Union[bytes, memoryview]) -> None:
    self.write(b"status=success\n")
    self.write(FLUSH_PACKET)
    if content and len(content):
      self.write(content)
    self.write(FLUSH_PACKET)
    self.write(FLUSH_PACKET)

  @contextmanager
  def writeSuccessFromFile(self, descPath: str) -> Generator[str, Any, None]:
    self.write(b"status=success\n")
    self.write(FLUSH_PACKET)

    with tempFilePath() as tfName:
      yield tfName
      chunkSize = 100_000_000
      inputSize = os.path.getsize(tfName)
      maybePrintGitRamWarning(inputSize=inputSize, descPath=descPath)
      with progressBar(inputSize, desc=(SFF["cyan"]("Preparing") + f" '{descPath}'")) as t:
        with open(tfName, "rb") as inFile:
          data = inFile.read(chunkSize)
          while data:
            self.write(data)
            t.update(len(data))
            data = inFile.read(chunkSize)

    self.write(FLUSH_PACKET)
    self.write(FLUSH_PACKET)
    print(SFF["purple"]("Storing") + f" '{descPath}' in git...", file=getStatusOutputStream(inputSize))

  def writeError(self) -> None:
    self.write(b"status=error\n")
    self.write(FLUSH_PACKET)

  def writeDelayed(self) -> None:
    self.write(b"status=delayed\n")
    self.write(FLUSH_PACKET)

  def scheduleSmudge(self, pathname: str, content: bytes) -> None:
    self.smudgeFutures.add(
        self.executor.submit(
            lambda pathname, content: [pathname, self.gitFilter.smudge(pathname, content)],  # type: ignore
            pathname,
            content))

  def _waitForAnyFuture(self) -> None:
    logger.info("Waiting futures=%d", len(self.smudgeFutures))
    done, not_done = wait(self.smudgeFutures, timeout=MAX_FILE_DOWNLOAD_WAIT_SEC, return_when=FIRST_COMPLETED)
    if len(done) == 0 and len(not_done) > 0:
      raise Exception("Failed to wait for file downloads")
    for future in done:
      pathname, content = future.result()
      self.smudgesAwaitingPickup[pathname] = content
    self.smudgeFutures = not_done

  def writeListOfAvailableBlobs(self) -> None:
    # If there are no available blobs, wait for some to come in
    if (len(self.smudgesAwaitingPickup) == 0):
      self._waitForAnyFuture()

    logger.info("availableBlobs=%d futures=%d", len(self.smudgesAwaitingPickup), len(self.smudgeFutures))
    for pathname in self.smudgesAwaitingPickup.keys():
      self.write(b"pathname=%s\n" % bytes(pathname, "utf8"))
    self.write(FLUSH_PACKET)
    self.write(b"status=success\n")
    self.write(FLUSH_PACKET)

  def getDelayedBlob(self, pathname: str) -> Union[bytes, memoryview]:
    # If asked for a blob we don't have, return empty
    # This seems to occur when there is a conflicted merge
    if pathname not in self.smudgesAwaitingPickup:
      return b''
    ret = self.smudgesAwaitingPickup[pathname]
    del (self.smudgesAwaitingPickup[pathname])
    return ret

  def filterProcess(self) -> NoReturn:
    """ https://git-scm.com/docs/gitattributes#_long_running_filter_process """
    try:
      self.versionHandshake()
      self.capabilitiesHandshake()

      while True:
        command, pathname, content, candelay = self.readCommand()
        try:
          if command == "clean":
            res = self.gitFilter.clean(pathname, content)
            self.writeSuccess(res)
          elif command == "smudge":
            if os.getenv("SKIP_SMUDGE") == "true":
              self.writeSuccess(content)
            elif candelay and self.gitFilter.smallEnoughToDelaySmudge(content):
              self.scheduleSmudge(pathname, content)
              self.writeDelayed()
            elif len(content) == 0:
              res = self.getDelayedBlob(pathname)
              self.writeSuccess(res)
            else:
              with self.writeSuccessFromFile(descPath=pathname) as fileName:
                self.gitFilter.smudgeToFile(pathname, content, toFile=fileName)
          elif command == "list_available_blobs":
            self.writeListOfAvailableBlobs()
        except Exception as e:
          logger.error(f"Error processing {command} on {pathname}", exc_info=e)
          self.writeError()
          raise
    except EOFError:
      exit(0)
    except:
      self.shutdown(True)
      raise

  def shutdown(self, force: bool = False) -> None:
    try:
      self.executor.shutdown(cancel_futures=True, wait=False)  # type: ignore
    except TypeError:  #Python <=3.8 doesn't have cancel_futures
      self.executor.shutdown(wait=False)
    finally:
      if force:
        try:
          # Stop threadpool executors
          import signal
          import os
          os.kill(os.getpid(), signal.SIGTERM)
        except:
          pass


def parseKv(s: bytes) -> Tuple[str, str]:
  [k, v] = s.split(b"=", 1)
  return (k.decode("utf-8"), v.decode("utf-8"))


def maybePrintGitRamWarning(inputSize: int, descPath: str) -> None:
  try:
    import psutil  # might not be installed
    approxGitBloatFactor = 1.4  # amount of memory used by git is larger than the raw file size
    ramRequired = int(approxGitBloatFactor * inputSize)
    availableMemory = psutil.virtual_memory().available
    if availableMemory > ramRequired:
      return
    print(SFF["red"]("Low RAM Warning:") +
          f" Git may need more RAM to store '{descPath}' ({sizeOfFmt(ramRequired).strip()} required)",
          file=getStatusOutputStream(inputSize))
  except:
    pass
