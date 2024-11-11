import subprocess
from typing import Optional


def getRepoRoot() -> Optional[str]:
  (exitCode, repoRoot) = subprocess.getstatusoutput('git rev-parse --show-toplevel')
  if exitCode != 0:
    return None
  return repoRoot
