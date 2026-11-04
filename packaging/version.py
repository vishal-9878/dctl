"""Print the build version and stamp it into src/dctl/__init__.py.
Uses the git tag (v1.2.3 -> 1.2.3) when building a tag, else the version already in the file."""
import os, re

p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "dctl", "__init__.py")
src = open(p).read()
ref = os.environ.get("GITHUB_REF", "")
ver = ref.rsplit("/", 1)[-1].lstrip("v") if ref.startswith("refs/tags/v") else re.search(r'__version__ = "(.+?)"', src)[1]
open(p, "w").write(re.sub(r'__version__ = ".*?"', f'__version__ = "{ver}"', src))
print(ver)
