import sys
from typing import Any, Callable, List, Optional, Union, Dict


def output(s: str) -> None:
  # TODO: Sometimes we should probably prefer stdout?
  print(s, file=sys.stderr)


def menu(prompt: str, opts: Dict[str, Callable[[], Any]], defaultAction: Optional[str]) -> Any:
  chosen = chooseOption(prompt, list(opts), defaultAction)
  if chosen is None:
    return None
  action = opts.get(chosen, None)
  if action is None:
    return None
  return action()


def chooseOption(text: str, options: List[str], default: Optional[Union[str, int]] = None) -> Optional[str]:
  if type(default) is int:
    default = options[default]
  defaultIdx = None
  output(f"{text}: ")
  for idx, opt in enumerate(options):
    if default == opt:
      isDefault = "*"
      defaultIdx = idx + 1
    else:
      isDefault = " "
    output(f"{isDefault}{idx + 1}: {opt}")
  defaultText = f" [{defaultIdx}]" if defaultIdx is not None else ""
  try:
    chosen = input(f"{text}{defaultText} > ").strip()
    if len(chosen) == 0 and defaultIdx is not None:
      chosenIdx = defaultIdx
    else:
      chosenIdx = int(chosen, 10)
      if chosenIdx == 0:
        return None
    return options[chosenIdx - 1]
  except EOFError:
    return None
  except (IndexError, ValueError):
    # TODO: Retry
    return None
