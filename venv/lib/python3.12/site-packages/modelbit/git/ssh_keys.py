from typing import List
import subprocess
import logging
import os
import re
from modelbit.ux import SHELL_FORMAT_FUNCS
from modelbit.api import MbApi
from modelbit.api.clone_api import CloneApi, SshKeyInfo
from modelbit.error import UserFacingError
from modelbit.telemetry import logEventToWeb

logger = logging.getLogger(__name__)

prettySshKeyDir = "~/.ssh"
sshKeyDir: str = os.path.expanduser(prettySshKeyDir)

# keep in sync with pattern in web/lib/types/workspace.ts
sshKeyPattern = r"^ssh-(?:ed25519|rsa|ecdsa|dsa)\s+[-A-Za-z0-9+/=]+(\s.*)?$"


def userInput(text: str, default: str) -> str:
  result = input(SHELL_FORMAT_FUNCS["purple"](text)).strip()
  if result == "":
    return default
  return result


def canCheckForSshKeys() -> bool:
  if "MB_SKIP_SSH_KEY_CHECK" in os.environ:  # for unblocking customers if there are bugs in our SSH key detection
    return False
  try:
    subprocess.check_output(["which", "ssh-keygen"])  # raises if which exit(1) when not finding ssh-keygen
  except:
    return False
  return os.name != "nt"


def scrubNickname(n: str) -> str:
  return re.sub(r"[^a-zA-Z0-9-_]", "-", n.strip())


def getHostname() -> str:
  import socket
  return socket.gethostname()


class LocalSshKey:

  def __init__(self, keyPath: str):
    self.keyPath = keyPath
    with open(self.keyPath, "r") as f:
      self.data = f.read()
    if not re.match(sshKeyPattern, self.data):
      raise ValueError(f"Invalid ssh key format: {self.keyPath}")
    self.sha256FingerPrint = self._genSha256Fingerprint()
    self.nickname = self._getNickname()

  def _genSha256Fingerprint(self) -> str:
    return subprocess.check_output(["ssh-keygen", "-l", "-f", self.keyPath]).decode()

  def _getNickname(self) -> str:
    nick = ""
    keyParts = self.data.strip().split(" ", maxsplit=2)
    if len(keyParts) == 3:
      nick = scrubNickname(keyParts[2])
    if nick == "":
      nick = scrubNickname(os.path.basename(self.keyPath))
    if nick == "":
      nick = scrubNickname(getHostname())
    return nick

  def prettyPath(self) -> str:
    return self.keyPath.replace(sshKeyDir, prettySshKeyDir)


def localSshPublicKeys() -> List[LocalSshKey]:
  if not os.path.exists(sshKeyDir) or not os.path.isdir(sshKeyDir):
    return []

  localKeys: List[LocalSshKey] = []
  for kName in sorted(os.listdir(sshKeyDir)):
    if kName.endswith(".pub"):
      try:
        localKeys.append(LocalSshKey(keyPath=os.path.join(sshKeyDir, kName)))
      except Exception as err:
        logger.warning(f"InvalidPublicKey: {err}")
        pass
  return localKeys


def hasSshKeyLocally(serverKeys: List[SshKeyInfo]) -> bool:
  for localKey in localSshPublicKeys():
    for sk in serverKeys:
      # fingerprints in modelbit are stripped-down from what ssh-keygen makes
      # and modelbit's fingerprint can have extra padding
      if sk.sha256Fingerprint.replace("=", "") in localKey.sha256FingerPrint:
        return True
  return False


def userUploadKey(api: MbApi) -> bool:
  print(f"An {SHELL_FORMAT_FUNCS['green']('SSH public key')} is required to clone from Modelbit.\n")
  return userUploadExistingKey(api) or userGenerateAndUploadNewKey(api)


def userUploadExistingKey(api: MbApi) -> bool:
  existingKeys = localSshPublicKeys()
  if len(existingKeys) == 0:
    return False

  if len(existingKeys) == 1:
    key = existingKeys[0]
    uploadKeyToModelbit(api=api, key=key, source="auto-upload")
    return True
  else:
    print("These SSH public keys are available:")
    for idx, key in enumerate(existingKeys):
      print(f"  {idx + 1}. {SHELL_FORMAT_FUNCS['green'](key.prettyPath())} ({key.nickname})")
    print("")
    whichKey = userInput(f"Which key do you want to use? [1/n] ", "1")
    if whichKey.isdigit():
      keyId = int(whichKey) - 1
      if keyId >= 0 and keyId < len(existingKeys):
        uploadKeyToModelbit(api=api,
                            key=existingKeys[keyId],
                            source=f"upload (choose from {len(existingKeys)})")
        return True

  print("")
  return False


def userGenerateAndUploadNewKey(api: MbApi) -> bool:
  os.makedirs(sshKeyDir, exist_ok=True)
  keyPath = os.path.join(sshKeyDir, "modelbit")
  publicKeyPath = f"{keyPath}.pub"
  if os.path.exists(keyPath):
    return False

  canCreate = userInput("Allow Modelbit to create a new SSH key? [Y/n] ", "Y")
  if not canCreate.lower().startswith("y"):
    return False

  try:
    subprocess.check_output(
        ["ssh-keygen", "-t", "ed25519", "-C", "modelbit-key", "-N", "", "-q", "-f", keyPath])
  except subprocess.CalledProcessError as err:
    raise UserFacingError(f"Unable to create an SSH key: {err}")

  if not os.path.exists(keyPath):
    raise UserFacingError(f"Unable to create an SSH private key: Not found: {keyPath}")
  if not os.path.exists(publicKeyPath):
    raise UserFacingError(f"Unable to create an SSH public key: Not found: {publicKeyPath}")

  uploadKeyToModelbit(api=api, key=LocalSshKey(publicKeyPath), source="generate-new")
  return True


def uploadKeyToModelbit(api: MbApi, key: LocalSshKey, source: str) -> None:
  print(f"Uploading SSH public key '{SHELL_FORMAT_FUNCS['green'](key.prettyPath())}' to Modelbit...")
  CloneApi(api).uploadSshKey(keyNickname=key.nickname, keyData=key.data)
  logEventToWeb(api=api, name="UploadSshKey", details={"keyNickname": key.nickname, "source": source})
  print("")
