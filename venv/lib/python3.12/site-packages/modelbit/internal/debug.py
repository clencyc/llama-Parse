from typing import Dict, Union, List
import subprocess
import sys
import os
from datetime import datetime

InfoType = Dict[str, Union[str, List[str], None]]


def runCommand(command: str) -> str:
  if sys.version_info < (3, 7):
    data = subprocess.run(command,
                          shell=True,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,
                          encoding="utf8")
  else:
    data = subprocess.run(command, capture_output=True, shell=True, text=True)
  return (data.stdout or data.stderr or "").strip()


def getDebugInfo(version: str) -> Dict[str, InfoType]:
  return {
      "modelbit": {
          "version": version,
          "path": runCommand("which modelbit"),
          "install": runCommand("pip show modelbit | grep Location"),
      },
      "python": {
          "path": runCommand("which python"),
          "version": runCommand("python --version"),
      },
      "python3": {
          "path": runCommand("which python3"),
          "version": runCommand("python3 --version"),
      },
      "git": {
          "path": runCommand("which git"),
          "version": runCommand("git --version"),
      },
      "sys": {
          "platform": sys.platform,
          "version": sys.version,
          "prefix": sys.prefix,
          "base_prefix": sys.base_prefix,
          "path": sys.path,
      },
      "env": {
          "$PATH": runCommand("echo $PATH").split(":"),
          "cwd": os.getcwd(),
          "user": runCommand("whoami"),
          "now": datetime.now().isoformat(),
      }
  }
