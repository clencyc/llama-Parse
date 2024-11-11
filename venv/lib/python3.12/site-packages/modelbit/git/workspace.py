#!/usr/bin/env python3

import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


def findWorkspace() -> str:
  workspaceId = None
  if "MB_WORKSPACE_ID" in os.environ:
    workspaceId = os.environ["MB_WORKSPACE_ID"]
    logger.info(f"Found workspace {workspaceId} in ENV")
  else:
    workspaceId = findWorkspaceIdInModelbitRepo()
  if workspaceId:
    return workspaceId
  raise KeyError("Workspace not found")


def findWorkspaceIdInModelbitRepo() -> Optional[str]:
  wsPath = ".workspace"
  if not os.path.exists(wsPath):
    topLevelDir = subprocess.getoutput('git rev-parse --show-toplevel')
    wsPath = os.path.join(topLevelDir, wsPath)
  if os.path.exists(wsPath):
    with open(wsPath, "r") as f:
      workspaceId = f.read().strip()
      logger.info(f"Found workspace {workspaceId} in {wsPath} file")
      return workspaceId
  return None


def findCurrentBranch() -> Optional[str]:
  try:
    # Ensure we are in a modelbit git repo before checking for the branch.
    # Otherwise we'll return the branch of any git repo you happen to be in.
    # This will throw on error
    if not findWorkspaceIdInModelbitRepo():
      return None

    ret = subprocess.getoutput('git branch --show-current')
    if "not a git repository" in ret:
      return None
    return ret
  except subprocess.SubprocessError as e:
    logger.info("Error finding current branch: %s", e)
    pass
  return None
