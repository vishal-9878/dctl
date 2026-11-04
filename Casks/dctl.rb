cask "dctl" do
  arch arm: "arm64", intel: "x86_64"

  version "0.3.0"
  sha256 arm:   "8487fe18f182ba5d543a6a9030914021647447ee01182b83782cc2de9e47da59",
         intel: "fc4a05f846fba787299965e049fd52572f199fb870a8809b7b30329cf226a45f"

  url "https://github.com/vishal-9878/dctl/releases/download/v#{version}/dctl-#{version}-macos-#{arch}.pkg"
  name "dctl"
  desc "Agentless desktop app for Docker and PM2 on many servers, over SSH"
  homepage "https://github.com/vishal-9878/dctl"

  livecheck do
    url :url
    strategy :github_latest
  end

  pkg "dctl-#{version}-macos-#{arch}.pkg"

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
