from typing import Dict, Union, Any, Optional, List, Callable, Tuple
import random, os, io
from html.parser import HTMLParser
from .utils import inNotebook, inIPythonTerminal, guessNotebookType

LIST_TITLES_WARNINGS = ("inconsistency", "inconsistencies")
LIST_TITLES_TIPS = ("tip", "tips")
LIST_TITLES_ERRORS = ("error", "errors")

COLORS = {
    "brand": "#845B99",
    "success": "#15803d",
    "error": "#E2548A",
}


class WarningErrorTip:

  def __init__(self, kind: str):
    self.kind = kind


class GenericError(WarningErrorTip):

  def __init__(self, errorText: str):
    super().__init__("GenericError")
    self.errorText = errorText


class MismatchedPackageWarning(WarningErrorTip):

  def __init__(self, desiredPackage: str, similarPackage: str):
    super().__init__("MismatchedPackageWarning")
    self.desiredPackage = desiredPackage
    self.similarPackage = similarPackage


class MissingPackageFromImportWarning(WarningErrorTip):

  def __init__(self, importedModule: str, localPackage: str):
    super().__init__("MissingPackageFromImportWarning")
    self.importedModule = importedModule
    self.localPackage = localPackage


class SkippedPackageWarning(WarningErrorTip):

  def __init__(self, importedModule: str, reason: str):
    super().__init__("SkippedPackageWarning")
    self.importedModule = importedModule
    self.reason = reason


class MissingExtraFileWarning(WarningErrorTip):

  def __init__(self, moduleName: str):
    super().__init__("MissingExtraFileWarning")
    self.moduleName = moduleName


class ProbablyNotAPackageWarning(WarningErrorTip):

  def __init__(self, packageName: str):
    super().__init__("ProbablyNotAPackageWarning")
    self.packageName = packageName


class ProbablyWantDataframeModeWarning(WarningErrorTip):

  def __init__(self) -> None:
    super().__init__("ProbablyWantDataframeModeWarning")


class ProbablyVersionWrong(WarningErrorTip):

  def __init__(self, packageName: str):
    super().__init__("ProbablyVersionWrong")
    self.packageName = packageName


class DifferentPythonVerWarning(WarningErrorTip):

  def __init__(self, desiredVersion: str, localVersion: str):
    super().__init__("DifferentPythonVerWarning")
    self.desiredVersion = desiredVersion
    self.localVersion = localVersion


class SnowparkBadPackageWarning(WarningErrorTip):

  def __init__(self, packageName: str):
    super().__init__("SnowparkBadPackageWarning")
    self.packageName = packageName


class SnowparkBadPackageVersionWarning(WarningErrorTip):

  def __init__(self, packageName: str, usingVersion: str, availableVersions: List[str]):
    super().__init__("SnowparkBadPackageVersionWarning")
    self.packageName = packageName
    self.usingVersion = usingVersion
    self.availableVersionsStr = ", ".join(availableVersions)


class SnowflakeRemovingMockFunctionValue(WarningErrorTip):

  def __init__(self, existingMockReturnValue: Any):
    super().__init__("SnowflakeRemovingMockFunctionValue")
    self.existingMockReturnValue: str = str(existingMockReturnValue)


class ProbablyWrongRequirement(WarningErrorTip):

  def __init__(self, packageName: str, reason: str):
    super().__init__("ProbablyWrongRequirement")
    self.packageName = packageName
    self.reason = reason


class GenericTip(WarningErrorTip):

  def __init__(self, tipText: str, docUrl: str):
    super().__init__("GenericTip")
    self.tipText = tipText
    self.docUrl = docUrl


class UserImage:

  def __init__(self, imageUrl: Optional[str], ownerName: Optional[str]):
    self.imageUrl = imageUrl
    self.ownerName = ownerName
    if self.imageUrl is None:
      self.imageUrl = "https://us-east-2.aws.modelbit.com/images/profile-placeholder.png"

  def __repr__(self) -> str:
    return self.ownerName or "N/A"


class TableHeader:
  LEFT = 'left'
  CENTER = 'center'
  RIGHT = 'right'

  def __init__(self, name: str, alignment: str = LEFT, isCode: bool = False, isPassFail: bool = False):
    self.name = name
    self.style = makeCssStyle({
        "text-align": alignment,
        "padding": "10px",
        "vertical-align": "middle",
        "line-height": 1,
    })
    self.isCode = isCode
    self.isPassFail = isPassFail
    self.alignment = alignment
    if alignment not in [self.LEFT, self.CENTER, self.RIGHT]:
      raise Exception(f'Unrecognized alignment: {alignment}')


TableType = Tuple[List[TableHeader], List[List[Union[str, UserImage]]]]


def renderTextTable(headers: List[TableHeader], rows: List[Any], maxWidth: int = 100) -> str:
  from texttable import Texttable
  table = Texttable(max_width=maxWidth)
  table.set_deco(Texttable.BORDER | Texttable.HEADER | Texttable.HLINES)  # type: ignore

  table.set_cols_align([h.alignment[0] for h in headers])  # type: ignore
  table.set_header_align([h.alignment[0] for h in headers])  # type: ignore
  table.add_rows([[h.name for h in headers]] + rows)  # type: ignore

  return table.draw()  # type: ignore


# From https://stackoverflow.com/questions/753052/strip-html-from-strings-in-python
class MLStripper(HTMLParser):

  def __init__(self) -> None:
    super().__init__()
    self.reset()
    self.strict = False
    self.convert_charrefs = True
    self.text = io.StringIO()

  def handle_data(self, data: str) -> None:
    self.text.write(data)

  def get_data(self) -> str:
    return self.text.getvalue()


def _baseElementStyles() -> Dict[str, str]:
  headerCss: Dict[str, Union[str, int]] = {
      "font-weight": "bold",
      "color": COLORS["brand"],
  }
  headerSuccess = headerCss.copy()
  headerSuccess.update({"color": COLORS["success"]})
  headerError = headerCss.copy()
  headerError.update({"color": COLORS["error"]})

  return {
      "base":
          makeCssStyle({}),
      "bottomSpacing":
          makeCssStyle({"margin": "0 0 20px 0"}),
      "container":
          makeCssStyle({"padding": "5px"}),
      "borderedGroup":
          makeCssStyle({
              "padding": "5px",
              "border-left": f"1px solid {COLORS['brand']}",
              "margin-bottom": "10px",
          }),
      "header":
          makeCssStyle(headerCss),
      "headerSuccess":
          makeCssStyle(headerSuccess),
      "headerError":
          makeCssStyle(headerError),
      "errorLabel":
          makeCssStyle({
              "color": COLORS["error"],
              "font-weight": "bold"
          }),
      "link":
          makeCssStyle({
              # "color": "#2563eb",
              "text-decoration": "underline",
              "cursor": "pointer"
          }),
      "ul":
          makeCssStyle({
              "background-color": "red",
              "padding": "10px",
          }),
      "li":
          makeCssStyle({
              "margin": "0 0 0 10px",
              "list-style": "circle",
          }),
      "code":
          makeCssStyle({
              "font-family": "monospace",
              "font-size": "13px",
              "font-weight": "400",
              "background-color": "rgba(209, 213, 219, 0.2)",
              "padding": "3px"
          }),
      "codeInTable":
          makeCssStyle({
              "font-family": "monospace",
              "color": COLORS["brand"],
              "font-size": "13px",
              "font-weight": "400",
              "white-space": "pre-wrap",
          }),
      "userImage":
          makeCssStyle({
              "display": "inline-block",
              "border-radius": "9999px",
              "width": "2rem",
              "height": "2rem",
              "background-color": "rgb(229 231 235)"
          })
  }


def makeCssStyle(styles: Dict[str, Union[str, int]]) -> str:
  baseStyles = {
      "margin": 0,
      "padding": 0,
      "line-height": 1.75,
      "font-size": "14px",
      "vertical-align": "baseline",
      "list-style": "none",
      "font-family": "Roboto, Arial, sans-serif",
      "background": "none",
  }
  baseStyles.update(styles)
  return "; ".join([f"{s[0]}: {s[1]}" for s in baseStyles.items()]) + ";"


NC = "\033[0m"
GREEN = "\033[1;32m"
BLUE_UNDERLINE = "\033[4;34m"
BOLD_WHITE = "\033[1m"
RED = "\033[1;31m"
BOLD_PURPLE = "\033[1;95m"
ITALIC = "\033[3m"
CYAN = "\033[1;36m"
YELLOW = "\033[1;33m"
BLUE = "\033[1;34m"

SHELL_FORMAT_FUNCS: Dict[str, Callable[[str], str]] = {
    "bold": lambda x: f"{BOLD_WHITE}{x}{NC}",
    "green": lambda x: f"{GREEN}{x}{NC}",
    "link": lambda x: f"{BLUE_UNDERLINE}{x}{NC}",
    "red": lambda x: f"{RED}{x}{NC}",
    "purple": lambda x: f"{BOLD_PURPLE}{x}{NC}",
    "cyan": lambda x: f"{CYAN}{x}{NC}",
    "yellow": lambda x: f"{YELLOW}{x}{NC}",
    "blue": lambda x: f"{BLUE}{x}{NC}",
    "italic": lambda x: f"{ITALIC}{x}{NC}",
}


def renderTemplate(templateName: str, styles: Dict[str, str] = {}, **kwargs: Any) -> str:
  stylesWithBase = _baseElementStyles()
  stylesWithBase.update(styles)
  from jinja2 import Environment, FileSystemLoader, select_autoescape, TemplateNotFound
  if useTxtMode():
    try:
      _jinjaTemplates = Environment(trim_blocks=True,
                                    lstrip_blocks=True,
                                    loader=FileSystemLoader(
                                        os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                                     'templates')))
      renderId = f"mb-{random.randint(1, 999999999)}"
      return _jinjaTemplates.get_template(f"{templateName}.txt.j2").render(styles=stylesWithBase,
                                                                           renderId=renderId,
                                                                           format=SHELL_FORMAT_FUNCS,
                                                                           renderTextTable=renderTextTable,
                                                                           isIPython=inIPythonTerminal(),
                                                                           **kwargs)
    except TemplateNotFound:
      # TODO: Strip html tags on fallback?
      pass

  _jinjaTemplates = Environment(loader=FileSystemLoader(
      os.path.join(os.path.dirname(os.path.realpath(__file__)), 'templates')),
                                autoescape=select_autoescape(['html.j2']))
  renderId = f"mb-{random.randint(1, 999999999)}"

  templateHelpers: List[str] = []
  if guessNotebookType() == "hex":
    templateHelpers.append(_jinjaTemplates.get_template(f"_hex-theme-fix.html.j2").render())

  return "\n".join(templateHelpers) + _jinjaTemplates.get_template(f"{templateName}.html.j2").render(
      styles=stylesWithBase, renderId=renderId, **kwargs)


def useTxtMode() -> bool:
  txtMode = os.getenv('MB_TXT_MODE')
  try:
    from IPython import display  # type: ignore
  except:
    txtMode = "1"

  if not inNotebook():
    txtMode = "1"
  return txtMode == "1"


def printTemplate(templateName: str,
                  displayId: Optional[str],
                  updateDisplayId: Optional[bool] = False,
                  styles: Dict[str, str] = {},
                  **kwargs: Any) -> None:
  txtMode = useTxtMode()

  content = renderTemplate(templateName, styles=styles, **kwargs)
  if txtMode:
    print(content)
  else:
    from IPython import display
    try:
      if guessNotebookType() == "hex":
        # Jupyter display clearing doesn't work in hex, and trying to can confuse hex
        display.display(display.HTML(content))  # type: ignore
      elif displayId is not None and updateDisplayId:
        display.update_display(display.HTML(content), display_id=displayId)  # type: ignore
      else:
        display.display(display.HTML(content), display_id=displayId, clear=bool(displayId))  # type: ignore
    except TypeError:
      # clear= is not supported on IPython 7.9.0, used by Google colab
      display.display(display.HTML(content), display_id=displayId)  # type: ignore
