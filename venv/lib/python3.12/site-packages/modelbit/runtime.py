from typing import Union, List, Dict, Any, Callable, Optional, cast, TYPE_CHECKING, Tuple
import re, json

from .environment import ALLOWED_PY_VERSIONS, getInstalledPythonVersion, listMissingPackagesFromImports, systemPackagesForPips, scrubUnwantedPackages, addDependentPackages, annotateSpecialPackages, orderPackages
from .helpers import RuntimePythonProps, getCurrentBranch, getMissingPackageWarningsFromEnvironment, getMissingPackageWarningsFromImportedModules, getProbablyNotAPackageWarnings, warningIfShouldBeUsingDataFrameWarning, mergePipPackageLists, pkgVersion, getVersionProbablyWrongWarnings, assertNoImpossiblePackages, getProbablyWrongRequirementWarning, getSkippedPrivatePackagesNotInMb
from .ux import DifferentPythonVerWarning, GenericError, WarningErrorTip, printTemplate, renderTemplate
from .collect_dependencies import getRuntimePythonProps, getFuncArgNames
from .source_generation import makeSourceFile, makeCreateJobRequest, DefaultModuleName
from .utils import guessNotebookType, guessOs, tryPickle, dumpJson
from .error import UserFacingError
from .snowpark import getSnowparkWarnings
import logging

from modelbit.api import DeployedRuntimeDesc, MbApi, RuntimeApi, BranchApi, SecretApi
from modelbit.internal import runtime_objects
from modelbit.internal.metadata import getMetadataWarnings
from modelbit.internal.auth import mbApi, maybeWarnAboutVersion
from modelbit.setup_manager import selectAndValidateSetups

if TYPE_CHECKING:
  import pandas
  import modelbit.jobs as m_jobs

logger = logging.getLogger(__name__)


# a bit of a hack for now
def _parseTimeFromDeployMessage(message: Optional[str]) -> Optional[str]:
  if not message:
    return None
  if "will be ready in" in message:
    return message.split("will be ready in")[1]
  return None


class RuntimeStatusNotes:

  def __init__(self, tips: List[WarningErrorTip], warnings: List[WarningErrorTip],
               errors: List[WarningErrorTip]):
    self.tips = tips
    self.warnings = warnings
    self.errors = errors
    self.deployable = len(errors) == 0

  def statusMsg(self) -> str:
    if self.deployable:
      return 'Ready'
    return 'Not Ready'

  def statusStyle(self) -> str:
    if self.deployable:
      return "color:green; font-weight: bold;"
    return "color:gray; font-weight: bold;"


class Runtime:

  def __init__(self,
               api: MbApi,
               name: Optional[str] = None,
               main_function: Optional[Callable[..., Any]] = None,
               python_version: Optional[str] = None,
               requirements_txt_filepath: Optional[str] = None,
               requirements_txt_contents: Optional[List[str]] = None,
               python_packages: Optional[List[str]] = None,
               system_packages: Optional[List[str]] = None,
               source_override: Optional[str] = None,
               dataframe_mode: bool = False,
               example_dataframe: Optional['pandas.DataFrame'] = None,
               common_files: Union[str, List[str], Dict[str, str], None] = None,
               extra_files: Union[str, List[str], Dict[str, str], None] = None,
               skip_extra_files_dependencies: bool = False,
               skip_extra_files_discovery: bool = False,
               snowflake_max_rows: Optional[int] = None,
               snowflake_mock_return_value: Optional[Any] = None,
               job: Optional['m_jobs.ModelbitJobWrapper'] = None,
               setupNames: Optional[Union[str, List[str]]] = None,
               require_gpu: Union[bool, str] = False):

    self._pythonPackages: Optional[List[str]] = None
    self._systemPackages: Optional[List[str]] = None
    self._deployName: Optional[str] = None
    self._deployFunc: Optional[Callable[..., Any]] = None
    self._sourceOverride = source_override
    self._dataframeMode = dataframe_mode
    self._dataframe_mode_columns: Optional[List[Dict[str, str]]] = None
    self._deploymentInfo: Optional[DeployedRuntimeDesc] = None

    self._require_gpu = 'T4' if require_gpu is True else require_gpu
    self._commonFiles = self._toCommonFilesDict(common_files)
    self._extraFiles = extra_files
    self._ignoreExtraFilesDependencies = skip_extra_files_dependencies
    self._ignoreExtraFileDiscoveries = skip_extra_files_discovery
    self._pythonVersion = getInstalledPythonVersion()
    self._job = job
    self._standaloneJob = bool(job and job.standalone)
    self._setups = selectAndValidateSetups(setupNames)
    self._api = api

    if snowflake_max_rows is not None and (type(snowflake_max_rows) is not int or snowflake_max_rows <= 0):
      raise UserFacingError(f"snowflake_max_rows must be a positive integer.")
    self._snowflakeMaxRows = snowflake_max_rows

    self._snowflakeMockReturnValue = snowflake_mock_return_value

    if name:
      self.set_name(name)
    if main_function:
      self._set_main_function(main_function)
    if python_version:
      self.set_python_version(python_version)
    if requirements_txt_filepath:
      self.set_requirements_txt(filepath=requirements_txt_filepath)
    if requirements_txt_contents:
      self.set_requirements_txt(contents=requirements_txt_contents)
    if python_packages is not None:
      self.set_python_packages(python_packages)
    if system_packages is not None:
      self.set_system_packages(system_packages)
    self._set_dataframeMode(dataframe_mode=dataframe_mode, example_dataframe=example_dataframe)

  def _repr_html_(self) -> str:
    return self.__repr__()

  def __repr__(self) -> str:
    if self._deployName is None:
      return ""
    elif self._deploymentInfo is None:
      return renderTemplate("deployment", name=self._deployName, version=None)
    else:
      return renderTemplate("deployment", name=self._deployName, version=self._deploymentInfo.version)

  def _set_dataframeMode(self, dataframe_mode: bool, example_dataframe: Optional['pandas.DataFrame']) -> None:
    if not dataframe_mode:
      if example_dataframe is not None:
        raise UserFacingError(
            "Setting dataframe_mode=True is required when passing the example_dataframe parameter")
      else:
        return
    elif example_dataframe is None:
      raise UserFacingError("The example_dataframe parameter is required when passing dataframe_mode=True")
    elif len(getFuncArgNames(self._deployFunc)) != 1:
      raise UserFacingError("Deployments using DataFrame Mode can only have one argument.")
    else:
      self._dataframeMode = True
      self._dataframe_mode_columns = self._collectDataFrameModeColumns(example_dataframe)

  def get_version(self) -> Optional[str]:
    if self._deploymentInfo is None:
      raise UserFacingError("Call .deploy() to create a version.")
    return self._deploymentInfo.version

  def set_name(self, name: str) -> None:
    if not re.match('^[a-zA-Z0-9_]+$', name):
      raise UserFacingError("Names should be alphanumeric with underscores.")
    self._deployName = name

  def set_python_version(self, version: str) -> None:
    if version not in ALLOWED_PY_VERSIONS:
      return self._selfError(f'Python version should be one of {ALLOWED_PY_VERSIONS}.')
    self._pythonVersion = version

  def set_requirements_txt(self,
                           filepath: Optional[str] = None,
                           contents: Optional[List[str]] = None) -> None:
    lines: List[str] = []
    if filepath != None and type(filepath) == str:
      f = open(filepath, "r")
      lines = [n.strip() for n in f.readlines()]
      return self.set_python_packages(lines)
    elif contents != None:
      return self.set_python_packages(contents)

  def set_python_packages(self, packages: Optional[List[str]]) -> None:
    if packages is None:
      self._pythonPackages = None
      return
    if type(packages) != list:
      raise UserFacingError("The python_packages parameter must be a list of strings.")
    for pkg in packages:
      if type(pkg) != str:
        raise UserFacingError("The python_packages parameters must be a list of strings.")
      if "\n" in pkg or "\r" in pkg:
        raise UserFacingError("The python_packages parameters cannot contain newlines")
      if "==" not in pkg and not pkg.startswith("https") and not pkg.startswith("git+https"):
        raise UserFacingError(
            f"The python_packages parameter '{pkg}' is formatted incorrectly. It should look like 'package-name==X.Y.Z'"
        )
      if pkg.startswith("sklearn=="):
        raise UserFacingError("The 'sklearn' package is deprecated. Use 'scikit-learn'.")
    self._pythonPackages = packages

  def set_system_packages(self, packages: Optional[List[str]]) -> None:
    if packages is None:
      self._systemPackages = None
      return
    if type(packages) != list:
      raise UserFacingError("The system_packages parameter must be a list of strings.")
    for pkg in packages:
      if type(pkg) != str:
        raise UserFacingError("The system_packages parameters must be a list of strings.")
      if not re.match("^[a-z0-9.+-]+$", pkg):
        # https://www.debian.org/doc/debian-policy/ch-controlfields.html#s-f-source
        raise UserFacingError(
            f"Names of system_packages must consist only of lower case letters, numbers, plus, minus, and periods. This package is invalid: {pkg}"
        )
      if "\n" in pkg or "\r" in pkg:
        raise UserFacingError("The system_packages parameters cannot contain newlines.")
    self._systemPackages = sorted(packages)

  def _set_main_function(self, func: Callable[..., Any]) -> None:
    self._deployFunc = func
    if callable(func) and self._deployName == None:
      self.set_name(func.__name__)

  def _getRequirementsTxt(self) -> Optional[str]:
    if self._pythonPackages:
      packages = "\n".join(orderPackages(self._pythonPackages))
      if "jax[" in packages:
        packages = "--find-links=https://storage.googleapis.com/jax-releases/jax_cuda_releases.html\n" + packages
      if "torch==" in packages or "torchvision==" in packages:
        packages = "--extra-index-url=https://download.pytorch.org/whl/\n" + packages
      if "llama_cpp_python==" in packages:
        packages = "--extra-index-url=https://abetlen.github.io/llama-cpp-python/whl/cu122/\n" + packages
      return packages
    else:
      return None

  def _getRuntimePythonProps(self) -> Tuple[Optional[RuntimePythonProps], Optional[GenericError]]:
    props: Optional[RuntimePythonProps] = None
    error: Optional[GenericError] = None

    try:
      localExtraFiles: Optional[List[str]] = list(runtime_objects.expandDirs(self._extraFiles).keys())
      if self._ignoreExtraFilesDependencies:
        localExtraFiles = None
      props = getRuntimePythonProps(self._deployFunc,
                                    sourceOverride=self._sourceOverride,
                                    job=self._job,
                                    extraFiles=localExtraFiles,
                                    dataframeMode=self._dataframeMode,
                                    setups=self._setups)
      if props and props.namespaceModules:
        assertNoImpossiblePackages(props.namespaceModules)
    except TypeError:
      raise
    except Exception as err:
      error = GenericError(str(err))
    return props, error

  def deploy(self) -> None:
    maybeWarnAboutVersion()
    rtProps, error = self._getRuntimePythonProps()
    self._decideAllPackages(rtProps, error)
    deployKind = "runtime-job" if self._deployFunc is None or self._standaloneJob else "runtime"

    if error is not None:
      printTemplate("error", None, errorText=error.errorText)
      return None
    if rtProps is None:
      printTemplate("error", None, errorText="Unable to continue because errors are present.")
      return None

    if self._snowflakeMockReturnValue is not None and rtProps.argTypes is not None and "return" in rtProps.argTypes:
      func_return_type = rtProps.argTypes['return']
      mock_return_type = type(self._snowflakeMockReturnValue).__name__
      if func_return_type != mock_return_type:
        raise UserFacingError(
            f"Type of snowflake_mock_return_value ({mock_return_type}) does not match function return type ({func_return_type})."
        )

    status = self._getStatusNotes(rtProps, error)
    if not status.deployable:
      logger.info("Unable to deploy: %s", status.errors)
      printTemplate("runtime-notes",
                    None,
                    deploymentName=self._deployName,
                    standaloneJob=self._standaloneJob,
                    warningsList=status.warnings,
                    tipsList=status.tips,
                    errorsList=status.errors)
      return None

    printTemplate(f"{deployKind}-deploying",
                  None,
                  deploymentName=None if self._standaloneJob else self._deployName,
                  jobName=self._job.desc.jobName if self._job is not None else None,
                  standaloneJob=self._standaloneJob,
                  warningsList=status.warnings,
                  tipsList=status.tips,
                  errorsList=status.errors)

    BranchApi(self._api).raiseIfProtected()
    integrationEnvVars = SecretApi(self._api).listIntegrationEnvVars()

    sourceFile = makeSourceFile(
        pyProps=rtProps,
        sourceFileName=DefaultModuleName,
        isJob=self._standaloneJob,
        integrationEnvVars=integrationEnvVars).asDict() if self._deployFunc is not None else None

    dataFiles = self._makeAndUploadDataFiles(rtProps)
    createRuntimeRequest: Dict[str, Any] = {
        "name": self._deployName,
        "dataFiles": dataFiles,
        "commonFiles": self._commonFiles,
        "pyState": {
            "sourceFile": sourceFile,
            "valueFiles": {},
            "name": rtProps.name,
            "module": DefaultModuleName,
            "argNames": rtProps.argNames,
            "argTypes": rtProps.argTypes,
            "requirementsTxt": self._getRequirementsTxt(),
            "pythonVersion": self._pythonVersion,
            "systemPackages": self._systemPackages,
            "dataframeModeColumns": self._dataframe_mode_columns,
            "snowflakeMaxRows": self._snowflakeMaxRows,
            "snowflakeMockReturnValue": self._snowflakeMockReturnValue,
        },
        "job": None if rtProps.job is None or self._standaloneJob else makeCreateJobRequest(rtProps.job),
        "source": {
            "os": guessOs(),
            "kind": guessNotebookType(),
            "version": pkgVersion
        },
    }
    if self._require_gpu:
      createRuntimeRequest['pyState']['capabilities'] = [f'gpu={self._require_gpu}']
    if len(dumpJson(createRuntimeRequest)) > 5_000_000:
      raise UserFacingError("Request size exceeds maximum allowed (5MB).")

    if self._standaloneJob:
      resp = RuntimeApi(self._api).createTrainingJob(getCurrentBranch(), createRuntimeRequest)
    else:
      resp = RuntimeApi(self._api).createRuntime(getCurrentBranch(), createRuntimeRequest)
    self._deploymentInfo = resp
    jobName = self._job.desc.jobName if self._job is not None else None
    printTemplate(f"{deployKind}-deployed",
                  None,
                  deploymentName=self._deployName,
                  deployMessage=resp.message,
                  jobName=jobName,
                  standaloneJob=self._standaloneJob,
                  deployTimeWords=_parseTimeFromDeployMessage(resp.message),
                  runtimeOverviewUrl=resp.runtimeOverviewUrl)
    return None

  def _makeAndUploadDataFiles(self, pyState: RuntimePythonProps) -> Dict[str, str]:
    dataFiles: Dict[str, str] = {}
    if pyState.namespaceVars:
      for nName, nVal in pyState.namespaceVars.items():
        uploadResult = runtime_objects.describeAndUploadRuntimeObject(self._api, nVal, tryPickle(nVal, nName),
                                                                      nName)
        if uploadResult:
          dataFiles[f"data/{nName.lower()}.pkl"] = uploadResult
    if pyState.extraDataFiles is not None:
      for nName, nObjBytes in pyState.extraDataFiles.items():
        uploadResult = runtime_objects.describeAndUploadRuntimeObject(self._api, nObjBytes[0], nObjBytes[1],
                                                                      nName)
        if uploadResult:
          dataFiles[nName] = uploadResult
    if pyState.extraSourceFiles:
      dataFiles.update(pyState.extraSourceFiles)
    if pyState.discoveredExtraFiles and not self._ignoreExtraFileDiscoveries:
      dataFiles.update(pyState.discoveredExtraFiles)
    if pyState.discoveredExtraDirs and not self._ignoreExtraFileDiscoveries:
      dataFiles.update(runtime_objects.prepareFileList(self._api, list(pyState.discoveredExtraDirs.keys())))
    dataFiles.update(runtime_objects.prepareFileList(self._api, self._extraFiles))
    if pyState.job is not None:
      dataFiles.update(self._makeAndUploadDataFiles(pyState.job.rtProps))
    return dataFiles

  def _toCommonFilesDict(self, files: Union[str, List[str], Dict[str, str], None]) -> Dict[str, str]:
    if files is None:
      return {}

    if type(files) is not list and type(files) is not dict and type(files) is not str:
      raise UserFacingError(f"The common_files parameter must be a list or dict. It is a {type(files)}.")

    if isinstance(files, str):
      return {files: files}

    if isinstance(files, List):
      return {path: path for path in files}

    return files

  def _selfError(self, txt: str) -> None:
    printTemplate("error", None, errorText=txt)
    return None

  def _decideAllPackages(self, rtPyProps: Optional[RuntimePythonProps],
                         propError: Optional[GenericError]) -> None:
    if propError is not None or rtPyProps is None:
      return
    missingModules = listMissingPackagesFromImports(rtPyProps.namespaceModules, self._pythonPackages)
    missingPips = list(set([m[1] for m in missingModules]))
    mergedPackageList = mergePipPackageLists(self._pythonPackages or [], sorted(missingPips))
    mergedPackageList = addDependentPackages(mergedPackageList)
    mergedPackageList = scrubUnwantedPackages(mergedPackageList)
    mergedPackageList = annotateSpecialPackages(mergedPackageList)
    self.set_python_packages(mergedPackageList)
    self.set_system_packages(systemPackagesForPips(self._pythonPackages, self._systemPackages))

  def _getStatusNotes(self, rtPyProps: Optional[RuntimePythonProps],
                      propError: Optional[GenericError]) -> RuntimeStatusNotes:
    tips: List[WarningErrorTip] = []
    warnings: List[WarningErrorTip] = []
    errors: List[WarningErrorTip] = []

    # Errors
    if not self._deployName:
      errors.append(GenericError("This deployment needs a name."))
    if propError is not None:
      errors.append(propError)
    if not self._api.isAuthenticated():
      errors.append(GenericError("You are not logged in to Modelbit. Please log in, then deploy."))

    # Warnings
    depPackages = self._pythonPackages
    if (rtPyProps is not None):
      warnings += getMissingPackageWarningsFromImportedModules(rtPyProps.namespaceModules, depPackages)
      # can re-enable once we also look in common files
      # warnings += getMissingLocalFileWarningsFromImportedModules(rtPyProps.namespaceModules, self._extraFiles)
      warnings += getProbablyNotAPackageWarnings(depPackages)
      warnings += getSkippedPrivatePackagesNotInMb(rtPyProps.namespaceModules)
      if not self._standaloneJob and not self._dataframeMode:
        warnings += warningIfShouldBeUsingDataFrameWarning(rtPyProps.argNames, rtPyProps.argTypes)
      warnings += getVersionProbablyWrongWarnings(depPackages)
      warnings += getProbablyWrongRequirementWarning(depPackages)
    warnings += getMissingPackageWarningsFromEnvironment(depPackages)
    if not self._standaloneJob:
      warnings += getSnowparkWarnings(self._api, self._pythonVersion, self._pythonPackages)
    if not self._standaloneJob and rtPyProps is not None and rtPyProps.name is not None:
      warnings += getMetadataWarnings(self._api,
                                      branch=getCurrentBranch(),
                                      deploymentName=rtPyProps.name,
                                      snowflakeMockReturnValue=self._snowflakeMockReturnValue)

    localPyVersion = getInstalledPythonVersion()
    if self._pythonVersion != localPyVersion:
      warnings.append(DifferentPythonVerWarning(self._pythonVersion, localPyVersion))

    return RuntimeStatusNotes(tips, warnings, errors)

  def _collectDataFrameModeColumns(self, df: 'pandas.DataFrame') -> List[Dict[str, Union[str, Any]]]:

    def shorten(value: Any) -> Union[str, Any]:
      if type(value) is str:
        return value[0:50]
      else:
        return value

    if len(getFuncArgNames(self._deployFunc)) != 1:
      raise UserFacingError(
          "When using dataframe_mode, the deploy function can only have one input argument.")
    config: List[Dict[str, Any]] = []
    examples: Optional[Dict[str, Any]] = None
    if len(df) > 0:
      examples = cast(Dict[str, Any], json.loads(df.head(1).to_json(orient="records"))[0])  # type: ignore
    for col in cast(List[str], list(df.columns)):  # type: ignore
      cf = {"name": col, "dtype": str(df[col].dtype)}
      if examples is not None:
        cf["example"] = shorten(examples[col])
      config.append(cf)
    return config


class Deployment(Runtime):

  def __init__(self,
               api: Optional[MbApi] = None,
               name: Optional[str] = None,
               deploy_function: Optional[Callable[..., Any]] = None,
               python_version: Optional[str] = None,
               requirements_txt_filepath: Optional[str] = None,
               requirements_txt_contents: Optional[List[str]] = None,
               python_packages: Optional[List[str]] = None,
               system_packages: Optional[List[str]] = None,
               source_override: Optional[str] = None,
               dataframe_mode: bool = False,
               example_dataframe: Optional['pandas.DataFrame'] = None,
               common_files: Union[str, List[str], Dict[str, str], None] = None,
               extra_files: Union[str, List[str], Dict[str, str], None] = None,
               skip_extra_files_dependencies: bool = False,
               skip_extra_files_discovery: bool = False,
               snowflake_max_rows: Optional[int] = None,
               snowflake_mock_return_value: Optional[Any] = None,
               job: Optional['m_jobs.ModelbitJobWrapper'] = None,
               setup: Optional[Union[str, List[str]]] = None,
               require_gpu: Union[bool, str] = False):
    if api is None:
      api = mbApi()
    Runtime.__init__(self,
                     api=api,
                     name=name,
                     main_function=deploy_function,
                     python_version=python_version,
                     requirements_txt_filepath=requirements_txt_filepath,
                     requirements_txt_contents=requirements_txt_contents,
                     python_packages=python_packages,
                     system_packages=system_packages,
                     source_override=source_override,
                     dataframe_mode=dataframe_mode,
                     example_dataframe=example_dataframe,
                     common_files=common_files,
                     extra_files=extra_files,
                     skip_extra_files_dependencies=skip_extra_files_dependencies,
                     skip_extra_files_discovery=skip_extra_files_discovery,
                     snowflake_max_rows=snowflake_max_rows,
                     snowflake_mock_return_value=snowflake_mock_return_value,
                     job=job,
                     setupNames=setup,
                     require_gpu=require_gpu)

  def set_deploy_function(self, func: Callable[..., Any]) -> None:
    self._set_main_function(func)


def add_objects(api: MbApi, deployment: str, values: Dict[str, Any]) -> None:
  """add_object takes the name of a deployment and map of object names to objects.
  These objects will be pickled and stored in `data/object.pkl`
  and can be read using modelbit.load_value('data/object.pkl).
  """
  dataFiles: Dict[str, str] = {}
  for [name, val] in values.items():
    uploadResult = runtime_objects.describeAndUploadRuntimeObject(api, val, tryPickle(val, name), name)
    if uploadResult:
      dataFiles[f"data/{name}.pkl"] = uploadResult
  return _changeFilesAndDeploy(api, deployment, dataFiles)


def add_files(api: MbApi,
              deployment: str,
              files: Union[str, List[str], Dict[str, str]],
              modelbit_file_prefix: Optional[str] = None,
              strip_input_path: Optional[bool] = False) -> None:
  """ add_files takes the name of a deployment and either a list of files or
  a dict of local paths to deployment paths.
  modelbit_file_prefix is an optional folder prefix added to all files when uploaded. For example (deployment="score", files=['myModel.pkl'], modelbit_file_prefix="data")
  would upload myModel.pkl in the current directory to data/myModel.pkl in the deployment named score.
  """

  BranchApi(api).raiseIfProtected()

  dataFiles = runtime_objects.prepareFileList(api,
                                              files,
                                              modelbit_file_prefix=modelbit_file_prefix,
                                              strip_input_path=strip_input_path)
  if len(files) == 0:
    raise UserFacingError("At least one file is required, but the list of files is empty.")
  if len(dumpJson(dataFiles)) > 5_000_000:
    raise UserFacingError("Total file size exceeds maximum allowed (5MB). Use git or add fewer files.")
  return _changeFilesAndDeploy(api, deployment, dataFiles)


def _changeFilesAndDeploy(api: MbApi, deployment: str, dataFiles: Dict[str, str]) -> None:
  resp = RuntimeApi(api).updateRuntime(getCurrentBranch(), deployment, dataFiles)

  printTemplate(f"runtime-deployed",
                None,
                deploymentName=deployment,
                deployMessage=resp.message,
                deployTimeWords=_parseTimeFromDeployMessage(resp.message),
                runtimeOverviewUrl=resp.runtimeOverviewUrl)
  return None


def copy_deployment(api: MbApi, fromBranch: str, toBranch: str, runtimeName: str,
                    runtimeVersion: Union[str, int]) -> None:
  resp = RuntimeApi(api).copyRuntime(fromBranch=fromBranch,
                                     toBranch=toBranch,
                                     runtimeName=runtimeName,
                                     runtimeVersion=runtimeVersion)
  printTemplate(f"runtime-deployed",
                None,
                deploymentName=runtimeName,
                deployMessage=None,
                deployTimeWords="a few seconds",
                runtimeOverviewUrl=resp.runtimeOverviewUrl)
