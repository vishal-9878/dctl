cask "dctl" do
  arch arm: "arm64", intel: "x86_64"

  version "0.4.3"
  sha256 arm:   "139423cc2de65e0f5aa15e773825db85bf922ed7457c1d9f1abf9584355abb01",
         intel: "add4a6ce01d1f79c4c24edf097965779028f0117f89ab0c3b05c9aac777b6d86"

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
