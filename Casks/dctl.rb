cask "dctl" do
  arch arm: "arm64", intel: "x86_64"

  version "0.4.4"
  sha256 arm:   "ed331160cd46dd658ae99dabb8eb813a0f2abc6a34eb72eeee14b0bd18450157",
         intel: "805776ef9418c199a9643f81d90e25b21ea2778e62b7f8379d79fc0d3dd5dcc0"

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
