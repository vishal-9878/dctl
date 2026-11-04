"""Write Casks/dctl.rb for a release (used by the release workflow).

    python3 packaging/homebrew/update_cask.py 0.4.0 <folder with the two .pkg files>
"""
import hashlib, os, sys

ver, folder = sys.argv[1], sys.argv[2]
sha = {}
for arch in ("arm64", "x86_64"):
    p = os.path.join(folder, f"dctl-{ver}-macos-{arch}.pkg")
    sha[arch] = hashlib.sha256(open(p, "rb").read()).hexdigest()

cask = f'''cask "dctl" do
  arch arm: "arm64", intel: "x86_64"

  version "{ver}"
  sha256 arm:   "{sha["arm64"]}",
         intel: "{sha["x86_64"]}"

  url "https://github.com/vishal-9878/dctl/releases/download/v#{{version}}/dctl-#{{version}}-macos-#{{arch}}.pkg"
  name "dctl"
  desc "Agentless desktop app for Docker and PM2 on many servers, over SSH"
  homepage "https://github.com/vishal-9878/dctl"

  livecheck do
    url :url
    strategy :github_latest
  end

  pkg "dctl-#{{version}}-macos-#{{arch}}.pkg"

  uninstall pkgutil: "io.github.vishal-9878.dctl",
            delete:  [
              "/Applications/dctl.app",
              "/usr/local/bin/dctl",
              "/usr/local/bin/dctl-ui",
              "/usr/local/lib/dctl",
            ]

  zap trash: [
    "~/.cache/dctl",
    "~/.config/dctl",
  ]
end
'''
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "Casks", "dctl.rb")
open(out, "w").write(cask)
print(f"wrote Casks/dctl.rb for {ver}")
