import argparse
import logging
import sys
from os import path
from typing import Any, List, Optional

from modelbit import add_model, add_package, delete_package, get_model, restart_deployment
from modelbit.api import MbApi
from modelbit.cli.deployment_create import createDeployment
from modelbit.cli.ui import output
from modelbit.error import ModelbitError, UserFacingError
from modelbit.git.clone import clone
from modelbit.git.common_files import linkCommonFiles
from modelbit.git.repo_helpers import getRepoRoot
from modelbit.git.validations import ensureGitRemoteAndCluster, validateRepo, writePreCommitHook
from modelbit.git.workspace import findWorkspace
from modelbit.internal.auth import mbApi
from modelbit.internal.local_config import deleteWorkspaceConfig, getWorkspaceConfig
from modelbit.telemetry import logEventToWeb
from modelbit.ux import SHELL_FORMAT_FUNCS

logger = logging.getLogger(__name__)


def _cloneAction(target_folder: str, origin: Optional[str], **_: Any) -> None:
  clone(target_folder, origin=origin)


def _versionAction(**_: Any) -> None:
  from modelbit import __version__
  print(__version__)
  exit(0)


def _debugAction(**_: Any) -> None:
  from modelbit import debug
  debug()
  exit(0)


def _validateAction(**_: Any) -> None:
  repoRoot = getRepoRoot()

  if repoRoot is None or not path.exists(path.join(repoRoot, ".workspace")):
    print("The current directory does not appear to be a Modelbit repository.")
    return

  workspaceId = findWorkspace()
  config = getWorkspaceConfig(workspaceId)
  if not config:
    logger.info("No workspace config. Showing login.")
    api = mbApi(source="clone")
  else:
    api = MbApi(config.gitUserAuthToken, config.cluster)
  writePreCommitHook()
  try:
    ensureGitRemoteAndCluster(api, workspaceId)
  except:
    logger.info("Clearing workspace config and trying again.")
    deleteWorkspaceConfig(workspaceId)
    api = mbApi(source="clone")
    ensureGitRemoteAndCluster(api, workspaceId)

  # When we're ready to enforce, uncomment the following
  # if validateRepo(api, wsPath):
  #   exit(0)
  # else:
  #   print("Unable to commit due to validation errors.\n", file=sys.stderr)
  #   exit(1)
  validateRepo(api, repoRoot)


def _cacheAction(command: str, workspace: Optional[str], **_: Any) -> None:
  from modelbit.internal import cache
  if command == "clear":
    cache.clearCache(workspace)
  elif command == "list":
    from .. import ux
    headers = [
        ux.TableHeader("Workspace"),
        ux.TableHeader("Kind"),
        ux.TableHeader("Name"),
        ux.TableHeader("Size", alignment=ux.TableHeader.RIGHT)
    ]
    print(ux.renderTextTable(headers, cache.getCacheList(workspace), maxWidth=120))


def _describeAction(filepath: Optional[str] = None, depth: int = 1, **_: Any) -> None:
  from modelbit.internal.describe import calcHash, describeFile
  from modelbit.internal.file_stubs import toYaml

  if filepath is not None:
    with open(filepath, "rb") as f:
      content = f.read()
  else:
    content = sys.stdin.buffer.read()

  print(toYaml(calcHash(content), len(content), describeFile(content, depth)))


def _gitfilterAction(**_: Any) -> None:
  from modelbit.git.filter_process import process
  process()


def _addPackageAction(pkgpath: str, force: bool, branch: str, **_: Any) -> None:
  add_package(pkgpath, force=force, branch=branch)


def _deletePackageAction(name: str, version: str, branch: str, **_: Any) -> None:
  delete_package(name=name, version=version, branch=branch)


def _listPackageAction(name: Optional[str], branch: str, **_: Any) -> None:
  from modelbit import _mbApi  # type: ignore
  from modelbit.internal.package import list_packages
  from .. import ux
  from modelbit.utils import timeago, sizeOfFmt
  headers = [
      ux.TableHeader("Name"),
      ux.TableHeader("Version"),
      ux.TableHeader("Added"),
      ux.TableHeader("Size", alignment=ux.TableHeader.RIGHT)
  ]
  pkgs = list_packages(name, _mbApi(branch=branch))
  packageList: List[List[Any]] = [[p.name, p.version,
                                   timeago(p.createdAtMs or 0),
                                   sizeOfFmt(p.size)] for p in pkgs]
  print(ux.renderTextTable(headers, packageList, maxWidth=120))


def _addModelAction(name: str, path: str, branch: str, **_: Any) -> None:
  add_model(name=name, file=path, branch=branch)


def _getModelAction(name: str, path: str, branch: str, **_: Any) -> None:
  get_model(name=name, file=path, branch=branch)


def _addCommonFilesAction(depName: Optional[str] = None,
                          jobName: Optional[str] = None,
                          pattern: Optional[str] = None,
                          **_: Any) -> None:
  linkCommonFiles(depName=depName, jobName=jobName, pattern=pattern)


def _restartDeploymentAction(branch: str, name: str, version: str, **_: Any) -> None:
  restart_deployment(branch=branch, deployment_name=name, version=version)


def _createDeploymentAction(name: str, **_: Any) -> None:
  createDeployment(name=name)


def initializeParser() -> argparse.ArgumentParser:
  visibleOptions: Optional[str] = '{clone,deployment,model,package,validate,version,add_common_files,debug}'
  if "-hh" in sys.argv:  # modelbit -hh to show full help
    visibleOptions = None
  parser = argparse.ArgumentParser(description="Modelbit CLI")
  subparsers = parser.add_subparsers(title='Actions', required=True, dest="action", metavar=visibleOptions)

  clone_parser = subparsers.add_parser('clone', help="Clone your Modelbit repository via git")
  clone_parser.set_defaults(func=_cloneAction)
  clone_parser.add_argument('target_folder', nargs='?', default="modelbit")
  clone_parser.add_argument(
      '--origin',
      metavar='{modelbit,github,gitlab,etc}',
      required=False,
      help=
      'Repository to clone. Set to modelbit, github, or gitlab to specify the remote to use. If not set, will show an interactive prompt'
  )
  _configureDeploymentParser(subparsers.add_parser('deployment', help="Manage deployments"))
  _configureModelParser(subparsers.add_parser('model', help="Manage file-based models in the registry"))
  _configurePackageParser(subparsers.add_parser('package', help="Add private packages to Modelbit"))

  subparsers.add_parser('validate',
                        help="Validate the files in this directory").set_defaults(func=_validateAction)
  subparsers.add_parser('version', help="Display Modelbit package version").set_defaults(func=_versionAction)
  subparsers.add_parser('debug', help="Print debug information").set_defaults(func=_debugAction)
  _configureAddCommonFilesParser(
      subparsers.add_parser('add_common_files',
                            help="Symlink common files into a deployment or training job"))

  cache_parser = subparsers.add_parser('cache')
  cache_parser.set_defaults(func=_cacheAction)
  cache_parser.add_argument('command', choices=['list', 'clear'])
  cache_parser.add_argument('workspace', nargs='?')

  describe_parser = subparsers.add_parser('describe')
  describe_parser.set_defaults(func=_describeAction)
  describe_parser.add_argument('filepath', nargs='?')
  describe_parser.add_argument('-d', '--depth', default=1, type=int)

  filter_parser = subparsers.add_parser('gitfilter')
  filter_parser.set_defaults(func=_gitfilterAction)
  filter_parser.add_argument('command', choices=['process'])

  return parser


def _configurePackageParser(package_parser: argparse.ArgumentParser) -> None:
  pkg_sub_parser = package_parser.add_subparsers(title="command", required=True, dest="command")

  add_pkg_parser = pkg_sub_parser.add_parser("add", help="Upload a private package")
  add_pkg_parser.add_argument('pkgpath')
  add_pkg_parser.add_argument('-f', '--force', action='store_true', help="Clobber existing versions")
  add_pkg_parser.add_argument('-b', '--branch', required=False, dest="branch", help="Specify git branch")
  add_pkg_parser.set_defaults(func=_addPackageAction)

  list_pkg_parser = pkg_sub_parser.add_parser("list", help="List private packages")
  list_pkg_parser.add_argument('name', nargs='?')
  list_pkg_parser.add_argument('-b', '--branch', required=False, dest="branch", help="Specify git branch")
  list_pkg_parser.set_defaults(func=_listPackageAction)

  delete_pkg_parser = pkg_sub_parser.add_parser("delete", help="Delete private package")
  delete_pkg_parser.add_argument('name')
  delete_pkg_parser.add_argument('version')
  delete_pkg_parser.add_argument('-b', '--branch', required=False, dest="branch", help="Specify git branch")
  delete_pkg_parser.set_defaults(func=_deletePackageAction)


def _configureAddCommonFilesParser(add_common_parser: argparse.ArgumentParser) -> None:
  add_common_parser.add_argument('-d',
                                 '--deployment',
                                 required=False,
                                 dest="depName",
                                 help="Specify the deployment to receive the common files")
  add_common_parser.add_argument('-j',
                                 '--job',
                                 required=False,
                                 dest="jobName",
                                 help="Specify the training job to receive the common files")
  add_common_parser.add_argument('-p',
                                 '--pattern',
                                 required=False,
                                 dest="pattern",
                                 help="Filter to certain common files or directories")
  add_common_parser.set_defaults(func=_addCommonFilesAction)


def _configureModelParser(model_parser: argparse.ArgumentParser) -> None:
  model_sub_parser = model_parser.add_subparsers(title="command", required=True, dest="command")

  model_add_parser = model_sub_parser.add_parser("add", help="Upload a model")
  model_add_parser.add_argument('-n', '--name', required=True, dest="name", help="Model name in the registry")
  model_add_parser.add_argument('-p',
                                '--path',
                                required=True,
                                dest="path",
                                help="Local filepath to the model file")
  model_add_parser.add_argument('-b', '--branch', required=False, dest="branch", help="Specify git branch")
  model_add_parser.set_defaults(func=_addModelAction)

  model_get_parser = model_sub_parser.add_parser("get", help="Download a model")
  model_get_parser.add_argument('-n', '--name', required=True, dest="name", help="Model name in the registry")
  model_get_parser.add_argument('-p',
                                '--path',
                                required=True,
                                dest="path",
                                help="Local filepath for the model file")
  model_get_parser.add_argument('-b', '--branch', required=False, dest="branch", help="Specify git branch")
  model_get_parser.set_defaults(func=_getModelAction)


def _configureDeploymentParser(dep_parser: argparse.ArgumentParser) -> None:
  dep_sub_parser = dep_parser.add_subparsers(title="command", required=True, dest="command")

  dep_restart_parser = dep_sub_parser.add_parser("restart", help="Restart a running deployment")
  dep_restart_parser.add_argument('-n', '--name', required=True, dest="name", help="Name of the deployment")
  dep_restart_parser.add_argument('-v',
                                  '--version',
                                  required=False,
                                  dest="version",
                                  help="Version of the deployment")
  dep_restart_parser.add_argument('-b', '--branch', required=False, dest="branch", help="Specify git branch")
  dep_restart_parser.set_defaults(func=_restartDeploymentAction)

  dep_restart_parser = dep_sub_parser.add_parser("create", help="Create the files for a new deployment")
  dep_restart_parser.add_argument('-n', '--name', required=True, dest="name", help="Name of the deployment")
  dep_restart_parser.set_defaults(func=_createDeploymentAction)


def processArgs() -> None:
  if sys.version_info < (3, 7):
    raise UserFacingError("Modelbit command line functionality is not supported on Python 3.6")
  parser = initializeParser()
  args = parser.parse_args()
  try:
    args.func(**vars(args))
  except TypeError as e:
    # Catch wrong number of args
    logger.info("Bad command line", exc_info=e)
    parser.print_help()
    logEventToWeb(userErrorMsg=str(e))
  except UserFacingError as e:
    red = SHELL_FORMAT_FUNCS["red"]
    output(f"{red('Error:')} {e}")
    logEventToWeb(userErrorMsg=str(e))
  except KeyboardInterrupt:
    exit(1)
  except ModelbitError as e:
    # Already printed an error
    logEventToWeb(userErrorMsg=str(e))
    exit(1)
  except Exception as e:
    logEventToWeb(userErrorMsg=str(e))
    raise
